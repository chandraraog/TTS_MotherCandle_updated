"""
42 -> 21 Cycle Research / Alert Engine

Purpose
-------
Research the same 42 -> 21 alternating HIGH/LOW structure on ANY
user-selected FYERS symbol and timeframe.

Pattern:
    HIGH -> 42 bars -> LOW -> 21 bars -> HIGH
    LOW  -> 42 bars -> HIGH -> 21 bars -> LOW

This file is intentionally separate from the live NIFTY 5-minute bot.
It is a research tool that can be run with different timeframes.

Examples:
    python cycle_42_21_any_timeframe.py --timeframe 5
    python cycle_42_21_any_timeframe.py --timeframe 15
    python cycle_42_21_any_timeframe.py --timeframe 60

Optional:
    --tolerance 0
    --tolerance 1
    --tolerance 2

The engine:
1. Gets historical candles from your existing FYERS client.
2. Detects alternating swing HIGH/LOW pivots.
3. Searches for 42-bar then 21-bar legs.
4. Prints origin/target date/time for each matching chain.
5. Projects the next 42 and 21 legs from the latest confirmed structure.
6. Optionally sends a Telegram alert for a newly detected current cycle.

Required existing project file:
    fyers_client.py

Expected:
    from fyers_client import get_fyers

Optional .env variables:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

No database. No Docker.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from fyers_client import get_fyers


SYMBOL_DEFAULT = "NSE:NIFTY50-INDEX"
FIRST_CYCLE = 42
SECOND_CYCLE = 21
IST = "Asia/Kolkata"

REPORT_FILE = Path(__file__).with_name("cycle_42_21_research_report.txt")


@dataclass(frozen=True)
class Pivot:
    index: int
    time: datetime
    kind: str
    high: float
    low: float
    price: float


@dataclass(frozen=True)
class Chain:
    p0: Pivot
    p1: Pivot
    p2: Pivot | None
    bars_42: int
    bars_21: int | None
    err_42: int
    err_21: int | None

    @property
    def exact(self) -> bool:
        return self.err_42 == 0 and self.err_21 == 0

    @property
    def near(self) -> bool:
        return self.err_42 != 0 or (self.err_21 or 0) != 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find repeating 42 -> 21 HIGH/LOW cycles using any FYERS timeframe."
    )
    parser.add_argument(
        "--timeframe",
        default="5",
        help="FYERS candle timeframe/resolution, e.g. 1, 3, 5, 15, 30, 60, 120, 240",
    )
    parser.add_argument(
        "--symbol",
        default=SYMBOL_DEFAULT,
        help=f"FYERS symbol (default: {SYMBOL_DEFAULT})",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=90,
        help="Calendar days of history to fetch (default: 90)",
    )
    parser.add_argument(
        "--pivot-strength",
        type=int,
        default=2,
        help="Pivot strength on each side (default: 2)",
    )
    parser.add_argument(
        "--tolerance",
        type=int,
        default=2,
        help="Allowed bar error around 42 and 21 (default: 2)",
    )
    parser.add_argument(
        "--alert",
        action="store_true",
        help="Send a Telegram alert for the latest valid projected cycle.",
    )
    return parser.parse_args()


def fetch_history(symbol: str, timeframe: str, start_date: str, end_date: str) -> pd.DataFrame:
    fyers = get_fyers()

    payload = {
        "symbol": symbol,
        "resolution": str(timeframe),
        "date_format": "1",
        "range_from": start_date,
        "range_to": end_date,
        "cont_flag": "1",
    }

    response = fyers.history(payload)

    candles = response.get("candles")
    if not candles:
        raise RuntimeError(f"FYERS returned no candles: {response}")

    df = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )

    df["datetime"] = (
        pd.to_datetime(df["timestamp"], unit="s", utc=True)
        .dt.tz_convert(IST)
        .dt.tz_localize(None)
    )

    df = (
        df.sort_values("datetime")
        .drop_duplicates("datetime")
        .reset_index(drop=True)
    )

    return df


def detect_pivots(df: pd.DataFrame, strength: int) -> list[Pivot]:
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()

    raw: list[Pivot] = []

    for i in range(strength, len(df) - strength):
        h = float(highs[i])
        l = float(lows[i])

        is_high = (
            h > highs[i - strength:i].max()
            and h > highs[i + 1:i + strength + 1].max()
        )
        is_low = (
            l < lows[i - strength:i].min()
            and l < lows[i + 1:i + strength + 1].min()
        )

        row = df.iloc[i]

        if is_high:
            raw.append(
                Pivot(
                    index=i,
                    time=row["datetime"].to_pydatetime(),
                    kind="HIGH",
                    high=h,
                    low=float(row["low"]),
                    price=h,
                )
            )
        elif is_low:
            raw.append(
                Pivot(
                    index=i,
                    time=row["datetime"].to_pydatetime(),
                    kind="LOW",
                    high=float(row["high"]),
                    low=l,
                    price=l,
                )
            )

    # Force alternating pivots. Keep the more extreme pivot when two
    # consecutive pivots of the same type appear.
    clean: list[Pivot] = []

    for pivot in raw:
        if not clean:
            clean.append(pivot)
            continue

        last = clean[-1]

        if pivot.kind != last.kind:
            clean.append(pivot)
        elif pivot.kind == "HIGH" and pivot.price > last.price:
            clean[-1] = pivot
        elif pivot.kind == "LOW" and pivot.price < last.price:
            clean[-1] = pivot

    return clean


def find_chains(pivots: list[Pivot], tolerance: int) -> list[Chain]:
    """
    Find candidate 42 -> 21 structures without requiring P0/P1 to be
    adjacent in the pivot list.

    This matches the methodology that produced the earlier July 5-minute
    results: search all alternating pivot pairs for the ~42-bar leg, then
    search forward for the opposite P2 around 21 bars after P1.

    Why this matters:
    a minor pivot can occur between the two major cycle points. Requiring
    consecutive pivots can therefore eliminate valid cycle matches.
    """
    chains: list[Chain] = []

    for i, p0 in enumerate(pivots):
        # Search all later opposite-type pivots for the first ~42-bar leg.
        for j in range(i + 1, len(pivots)):
            p1 = pivots[j]

            if p1.kind == p0.kind:
                continue

            bars_42 = p1.index - p0.index
            err_42 = bars_42 - FIRST_CYCLE

            if bars_42 > FIRST_CYCLE + tolerance:
                # Later pivots will only be farther away.
                break

            if abs(err_42) > tolerance:
                continue

            # Search forward for the next opposite pivot around 21 bars
            # from P1. Same-type pivots are skipped.
            best_p2: Pivot | None = None
            best_err_21: int | None = None

            for k in range(j + 1, len(pivots)):
                p2 = pivots[k]

                if p2.kind == p1.kind:
                    continue

                bars_21 = p2.index - p1.index
                err_21 = bars_21 - SECOND_CYCLE

                if abs(err_21) <= tolerance:
                    if (
                        best_p2 is None
                        or abs(err_21) < abs(best_err_21 or 10**9)
                    ):
                        best_p2 = p2
                        best_err_21 = err_21

                if bars_21 > SECOND_CYCLE + tolerance:
                    break

            chains.append(
                Chain(
                    p0=p0,
                    p1=p1,
                    p2=best_p2,
                    bars_42=bars_42,
                    bars_21=None if best_p2 is None else best_p2.index - p1.index,
                    err_42=err_42,
                    err_21=best_err_21,
                )
            )

    # Deduplicate same P0/P1/P2 result combinations.
    unique = {}
    for c in chains:
        key = (
            c.p0.index,
            c.p1.index,
            None if c.p2 is None else c.p2.index,
        )
        unique[key] = c

    return sorted(
        unique.values(),
        key=lambda c: (c.p1.index, c.p2.index if c.p2 else 10**9),
    )


def project_next(chain: Chain) -> list[dict]:
    """
    From P2, continue the user's repeating sequence:
        42 -> 21 -> 42 -> 21
    If P2 is not yet available, project from P1 into the 21 leg.
    """
    source = chain.p2 if chain.p2 is not None else chain.p1

    if chain.p2 is None:
        next_kind = "LOW" if source.kind == "HIGH" else "HIGH"
        return [
            {
                "source": source,
                "target_kind": next_kind,
                "bars": SECOND_CYCLE,
                "phase": "PENDING_21",
            }
        ]

    next_kind = "LOW" if source.kind == "HIGH" else "HIGH"

    return [
        {
            "source": source,
            "target_kind": next_kind,
            "bars": FIRST_CYCLE,
            "phase": "NEXT_42",
        },
        {
            "source": source,
            "target_kind": next_kind,
            "bars": FIRST_CYCLE + SECOND_CYCLE,
            "phase": "NEXT_42_PLUS_21",
        },
    ]


def future_datetime_from_bars(
    df: pd.DataFrame,
    source_index: int,
    bars_forward: int,
) -> datetime:
    target_index = source_index + bars_forward

    # If future index is inside fetched data, use the actual candle timestamp.
    if target_index < len(df):
        return df.iloc[target_index]["datetime"].to_pydatetime()

    # Otherwise estimate future session timestamps from NIFTY's normal
    # 5-minute-session structure: 75 candles/day, session approximately 09:15-15:30.
    # This is only for display/projection; future live data should replace it
    # when it becomes available.
    approx_minutes = 5 * bars_forward
    base = df.iloc[source_index]["datetime"].to_pydatetime()
    candidate = base + timedelta(minutes=approx_minutes)

    # Move weekends forward.
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)

    return candidate


def fmt_chain(chain: Chain) -> str:
    if chain.p2 is None:
        return (
            f"{chain.p0.kind} {chain.p0.time:%d-%b-%Y %H:%M}"
            f" --{chain.bars_42}--> "
            f"{chain.p1.kind} {chain.p1.time:%d-%b-%Y %H:%M}"
            f" | 42err={chain.err_42:+d}"
            f" | 21 pending"
        )

    return (
        f"{chain.p0.kind} {chain.p0.time:%d-%b-%Y %H:%M}"
        f" --{chain.bars_42}--> "
        f"{chain.p1.kind} {chain.p1.time:%d-%b-%Y %H:%M}"
        f" --{chain.bars_21}--> "
        f"{chain.p2.kind} {chain.p2.time:%d-%b-%Y %H:%M}"
        f" | errors={chain.err_42:+d}/{(chain.err_21 or 0):+d}"
    )


def telegram_send(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        print("\n[Telegram not configured]\n" + message)
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(
        url,
        data={"chat_id": chat_id, "text": message},
        timeout=10,
    )
    response.raise_for_status()
    print("Telegram message sent successfully.")


def main() -> None:
    args = parse_args()

    today = datetime.now().date()
    start_date = today - timedelta(days=args.days_back)

    print("=" * 92)
    print("42 -> 21 MULTI-TIMEFRAME CYCLE RESEARCH")
    print("=" * 92)
    print(f"Symbol          : {args.symbol}")
    print(f"Timeframe       : {args.timeframe}")
    print(f"History         : {start_date} .. {today}")
    print(f"Pivot strength  : {args.pivot_strength}")
    print(f"Tolerance       : +/- {args.tolerance} bars")
    print("")

    df = fetch_history(
        args.symbol,
        args.timeframe,
        start_date.isoformat(),
        today.isoformat(),
    )

    print(f"FYERS candles   : {len(df)}")

    pivots = detect_pivots(df, args.pivot_strength)
    chains = find_chains(pivots, args.tolerance)

    exact = [c for c in chains if c.exact]
    near = [c for c in chains if not c.exact]

    print(f"Pivots          : {len(pivots)}")
    print(f"42->21 chains   : {len(chains)}")
    print(f"Exact chains    : {len(exact)}")
    print(f"Near chains     : {len(near)}")
    print("")

    print("CYCLE MATCHES")
    print("-" * 92)

    for number, chain in enumerate(chains, start=1):
        print(f"{number:03d}  {fmt_chain(chain)}")

    print("")
    print("LATEST / CURRENT CYCLE")
    print("-" * 92)

    if chains:
        latest_chain = chains[-1]
        print(fmt_chain(latest_chain))

        print("")
        print("FORWARD PROJECTION")
        print("-" * 92)

        for projection in project_next(latest_chain):
            source = projection["source"]
            target_time = future_datetime_from_bars(
                df,
                source.index,
                projection["bars"],
            )
            print(
                f"{projection['phase']:>18} | "
                f"{source.kind} {source.time:%d-%b-%Y %H:%M} "
                f"+{projection['bars']} bars -> "
                f"{projection['target_kind']} ~ {target_time:%d-%b-%Y %H:%M}"
            )

        if args.alert:
            p = project_next(latest_chain)[0]
            source = p["source"]
            target_time = future_datetime_from_bars(
                df,
                source.index,
                p["bars"],
            )

            message = (
                "42->21 Cycle Alert\n\n"
                f"Timeframe : {args.timeframe}\n"
                f"Current : {source.kind} {source.time:%d-%b-%Y %H:%M}\n"
                f"Next    : {p['target_kind']}\n"
                f"Expected: {target_time:%d-%b-%Y %H:%M}\n"
                f"Cycle   : {p['bars']} bars"
            )
            telegram_send(message)
    else:
        print("No 42->21 chain found in the selected timeframe/history.")

    report = [
        "42 -> 21 MULTI-TIMEFRAME CYCLE RESEARCH",
        f"Symbol: {args.symbol}",
        f"Timeframe: {args.timeframe}",
        f"History: {start_date} .. {today}",
        f"FYERS candles: {len(df)}",
        f"Pivots: {len(pivots)}",
        f"Exact chains: {len(exact)}",
        f"Near chains: {len(near)}",
        "",
        *[fmt_chain(c) for c in chains],
    ]
    REPORT_FILE.write_text("\n".join(report) + "\n", encoding="utf-8")

    print("")
    print(f"Report saved: {REPORT_FILE}")


if __name__ == "__main__":
    main()
