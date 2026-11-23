from __future__ import annotations

import argparse
import json
import os
import time as time_module
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from fyers_client import get_fyers


# ============================================================
# NIFTY 5-MINUTE ADVANCED CYCLE BOT
# ============================================================

SYMBOL = "NSE:NIFTY50-INDEX"
RESOLUTION = "5"
IST = "Asia/Kolkata"

FIRST_BARS = 42
SECOND_BARS = 21

# Research matching tolerance.
# 0 = exact 42/21 only
# 1 = +/- 1 bar
# 2 = +/- 2 bars
CYCLE_TOLERANCE = 0

# Pivot confirmation strength.
PIVOT_STRENGTH = 2

# Telegram alert control:
#
# GENERAL -> alert on every new running cycle/status
# SPECIAL -> alert only Mother Candle / TPS / Mother Candle + TPS
# MOTHER  -> alert only Mother Candle conditions
# TPS     -> alert only Time Price Square conditions
#
# Recommended for live trading alerts:
ALERT_MODE = "GENERAL"

REPORT_FILE = Path(__file__).with_name("advanced_cycle_5m_report.txt")
STATE_FILE = Path(__file__).with_name("advanced_cycle_5m_state.json")


@dataclass(frozen=True)
class Pivot:
    index: int
    time: datetime
    kind: str
    high: float
    low: float
    price: float


@dataclass(frozen=True)
class CycleLeg:
    p0: Pivot
    p1: Pivot
    bars: int
    target_bars: int
    error: int


@dataclass(frozen=True)
class CycleChain:
    p0: Pivot
    p1: Pivot
    p2: Optional[Pivot]
    leg1: CycleLeg
    leg2: Optional[CycleLeg]
    confidence: str


# ============================================================
# FYERS
# ============================================================

def fetch_history(start_date: str, end_date: str) -> pd.DataFrame:
    fyers = get_fyers()

    request = {
        "symbol": SYMBOL,
        "resolution": RESOLUTION,
        "date_format": "1",
        "range_from": start_date,
        "range_to": end_date,
        "cont_flag": "1",
    }

    response = fyers.history(request)

    candles = response.get("candles")
    if not candles:
        raise RuntimeError(f"FYERS history error: {response}")

    df = pd.DataFrame(
        candles,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
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


# ============================================================
# PIVOTS
# ============================================================

def detect_pivots(
    df: pd.DataFrame,
    strength: int = PIVOT_STRENGTH,
) -> list[Pivot]:
    """
    Detect confirmed HIGH/LOW pivots.

    strength must be >= 1. A defensive guard is included so a bad config
    cannot produce NumPy zero-size reduction errors.
    """

    if strength < 1:
        raise ValueError(
            f"PIVOT_STRENGTH must be >= 1, got {strength}. "
            "Use PIVOT_STRENGTH = 2 for the current production bot."
        )

    if len(df) < (2 * strength + 1):
        return []

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()

    raw: list[Pivot] = []

    for i in range(strength, len(df) - strength):
        left_high = highs[i - strength:i]
        right_high = highs[i + 1:i + strength + 1]
        left_low = lows[i - strength:i]
        right_low = lows[i + 1:i + strength + 1]

        # Defensive check; these should be non-empty when strength >= 1.
        if (
            left_high.size == 0
            or right_high.size == 0
            or left_low.size == 0
            or right_low.size == 0
        ):
            continue

        h = float(highs[i])
        l = float(lows[i])

        is_high = (
            h > left_high.max()
            and h > right_high.max()
        )

        is_low = (
            l < left_low.min()
            and l < right_low.min()
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

    # Keep pivots alternating.
    # If two same-type pivots appear, keep the more extreme one.
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


def find_42_21_chains(
    pivots: list[Pivot],
    tolerance: int = CYCLE_TOLERANCE,
) -> list[CycleChain]:
    """
    Find:

        HIGH -> ~42 bars -> LOW -> ~21 bars -> HIGH
        LOW  -> ~42 bars -> HIGH -> ~21 bars -> LOW

    Important:
    P0 and P1 do NOT have to be consecutive detected pivots.
    Intermediate smaller swings are allowed.

    Bar count always uses the actual FYERS candle index.
    """

    chains: list[CycleChain] = []

    for i, p0 in enumerate(pivots):
        for j in range(i + 1, len(pivots)):
            p1 = pivots[j]

            if p0.kind == p1.kind:
                continue

            bars_42 = p1.index - p0.index
            err_42 = bars_42 - FIRST_BARS

            if bars_42 > FIRST_BARS + tolerance:
                break

            if abs(err_42) > tolerance:
                continue

            best_p2: Optional[Pivot] = None
            best_err_21: Optional[int] = None

            for k in range(j + 1, len(pivots)):
                p2 = pivots[k]

                if p2.kind == p1.kind:
                    continue

                bars_21 = p2.index - p1.index
                err_21 = bars_21 - SECOND_BARS

                if abs(err_21) <= tolerance:
                    if (
                        best_p2 is None
                        or abs(err_21) < abs(best_err_21 or 999999)
                    ):
                        best_p2 = p2
                        best_err_21 = err_21

                if bars_21 > SECOND_BARS + tolerance:
                    break

            leg1 = CycleLeg(
                p0=p0,
                p1=p1,
                bars=bars_42,
                target_bars=FIRST_BARS,
                error=err_42,
            )

            if best_p2 is None:
                chains.append(
                    CycleChain(
                        p0=p0,
                        p1=p1,
                        p2=None,
                        leg1=leg1,
                        leg2=None,
                        confidence="42_LEG_ONLY",
                    )
                )
                continue

            leg2 = CycleLeg(
                p0=p1,
                p1=best_p2,
                bars=best_p2.index - p1.index,
                target_bars=SECOND_BARS,
                error=best_err_21 or 0,
            )

            confidence = (
                "EXACT"
                if leg1.error == 0 and leg2.error == 0
                else "NEAR"
            )

            chains.append(
                CycleChain(
                    p0=p0,
                    p1=p1,
                    p2=best_p2,
                    leg1=leg1,
                    leg2=leg2,
                    confidence=confidence,
                )
            )

    # De-duplicate identical structures.
    unique: dict[tuple[int, int, Optional[int]], CycleChain] = {}

    for chain in chains:
        key = (
            chain.p0.index,
            chain.p1.index,
            None if chain.p2 is None else chain.p2.index,
        )
        unique[key] = chain

    return sorted(
        unique.values(),
        key=lambda c: (
            c.p1.index,
            c.p2.index if c.p2 else 10**9,
        ),
    )


# ============================================================
# FUTURE PROJECTION
# ============================================================

def future_time_from_bar_index(
    df: pd.DataFrame,
    target_index: int,
) -> Optional[datetime]:
    """
    Historical:
        use exact FYERS timestamp.

    Future:
        estimate using the NIFTY 5-minute session:
            09:15 ... 15:25
            75 candles/session

    Weekends are skipped.

    For future market holidays, the timestamp is only a projection.
    When FYERS later returns the actual candle, the real timestamp wins.
    """

    if target_index < 0:
        return None

    if target_index < len(df):
        return df.iloc[target_index]["datetime"].to_pydatetime()

    last_time = df.iloc[-1]["datetime"].to_pydatetime()

    # Determine current slot in the 75-candle NIFTY session.
    session_open = last_time.replace(
        hour=9,
        minute=15,
        second=0,
        microsecond=0,
    )

    elapsed_minutes = int(
        (last_time - session_open).total_seconds() // 60
    )

    current_slot = max(
        0,
        min(74, elapsed_minutes // 5),
    )

    extra_bars = target_index - (len(df) - 1)

    target_date = last_time.date()
    target_slot = current_slot

    while extra_bars > 0:
        target_slot += 1

        if target_slot >= 75:
            target_date += timedelta(days=1)

            while target_date.weekday() >= 5:
                target_date += timedelta(days=1)

            target_slot = 0

        extra_bars -= 1

    return datetime.combine(
        target_date,
        datetime.min.time(),
    ).replace(
        hour=9,
        minute=15,
    ) + timedelta(
        minutes=5 * target_slot
    )


def project_from_chain(
    chain: CycleChain,
) -> list[dict]:
    """
    Continue:

        42 -> 21 -> 42 -> 21 ...
    """

    if chain.p2 is None:
        source = chain.p1
        target_kind = "LOW" if source.kind == "HIGH" else "HIGH"

        return [
            {
                "reason": "PENDING_21",
                "source": source,
                "target_kind": target_kind,
                "bars": SECOND_BARS,
                "target_index": source.index + SECOND_BARS,
            }
        ]

    source = chain.p2
    target_kind = "LOW" if source.kind == "HIGH" else "HIGH"

    return [
        {
            "reason": "NEXT_42",
            "source": source,
            "target_kind": target_kind,
            "bars": FIRST_BARS,
            "target_index": source.index + FIRST_BARS,
        },
        {
            "reason": "NEXT_42_PLUS_21",
            "source": source,
            "target_kind": target_kind,
            "bars": FIRST_BARS + SECOND_BARS,
            "target_index": source.index + FIRST_BARS + SECOND_BARS,
        },
    ]


# ============================================================
# MOTHER CANDLE
# ============================================================

def find_convergence(
    df: pd.DataFrame,
    projections: list[dict],
    tolerance_minutes: int = 5,
) -> list[dict]:
    projected: list[dict] = []

    for item in projections:
        target_time = future_time_from_bar_index(
            df,
            item["target_index"],
        )

        if target_time is None:
            continue

        projected.append(
            {
                **item,
                "time": target_time,
            }
        )

    projected.sort(key=lambda x: x["time"])

    groups: list[list[dict]] = []

    for item in projected:
        if not groups:
            groups.append([item])
            continue

        previous = groups[-1][-1]

        if (
            abs(
                (item["time"] - previous["time"]).total_seconds()
            )
            <= tolerance_minutes * 60
        ):
            groups[-1].append(item)
        else:
            groups.append([item])

    results: list[dict] = []

    for group in groups:
        if len(group) >= 2:
            results.append(
                {
                    "time": group[0]["time"],
                    "count": len(group),
                    "mother": True,
                    "projections": group,
                }
            )

    return results


# ============================================================
# TIME PRICE SQUARE
# ============================================================

def time_price_square(
    df: pd.DataFrame,
    timing_time: datetime,
    lookback_bars: int = FIRST_BARS,
) -> dict:
    """
    Research definition currently used by the project:

    The current timing candle's full High/Low range is inside the
    previous 42-bar price range.

    This is evaluated only on an already-formed FYERS candle.
    """

    matches = df.index[df["datetime"] == timing_time]

    if len(matches) == 0:
        return {
            "available": False,
            "square": False,
        }

    idx = int(matches[0])

    if idx <= 0:
        return {
            "available": False,
            "square": False,
        }

    start = max(0, idx - lookback_bars)
    previous = df.iloc[start:idx]
    current = df.iloc[idx]

    previous_low = float(previous["low"].min())
    previous_high = float(previous["high"].max())

    current_low = float(current["low"])
    current_high = float(current["high"])

    square = (
        previous_low <= current_low <= previous_high
        and previous_low <= current_high <= previous_high
    )

    return {
        "available": True,
        "square": square,
        "previous_low": previous_low,
        "previous_high": previous_high,
        "current_low": current_low,
        "current_high": current_high,
    }


# ============================================================
# BACKEND REPORT
# ============================================================

def write_backend_report(
    df: pd.DataFrame,
    pivots: list[Pivot],
    chains: list[CycleChain],
    projections: list[dict],
    status: str,
) -> None:

    lines = [
        "=" * 100,
        "NIFTY 5-MINUTE ADVANCED CYCLE - BACKEND / RESEARCH DETAILS",
        "=" * 100,
        f"FYERS candles       : {len(df)}",
        f"Last candle         : {df.iloc[-1]['datetime']:%d-%b-%Y %H:%M}",
        f"Pivot count         : {len(pivots)}",
        f"Cycle tolerance     : +/- {CYCLE_TOLERANCE} bars",
        f"Current status      : {status}",
        "",
        "RECENT 42 -> 21 CHAINS",
        "-" * 100,
    ]

    recent = chains[-30:]

    if not recent:
        lines.append("No 42 -> 21 candidates found.")

    for number, chain in enumerate(
        recent,
        start=max(1, len(chains) - len(recent) + 1),
    ):
        if chain.p2 is None:
            lines.append(
                f"{number:03d}  "
                f"{chain.p0.kind} {chain.p0.time:%d-%b-%Y %H:%M}"
                f" --{chain.leg1.bars}--> "
                f"{chain.p1.kind} {chain.p1.time:%d-%b-%Y %H:%M}"
                f" | 42 error={chain.leg1.error:+d}"
                f" | 21 pending"
            )
        else:
            lines.append(
                f"{number:03d}  "
                f"{chain.p0.kind} {chain.p0.time:%d-%b-%Y %H:%M}"
                f" --{chain.leg1.bars}--> "
                f"{chain.p1.kind} {chain.p1.time:%d-%b-%Y %H:%M}"
                f" --{chain.leg2.bars}--> "
                f"{chain.p2.kind} {chain.p2.time:%d-%b-%Y %H:%M}"
                f" | errors={chain.leg1.error:+d}/"
                f"{chain.leg2.error:+d}"
                f" | {chain.confidence}"
            )

    exact_count = sum(
        1 for c in chains
        if c.p2 is not None and c.confidence == "EXACT"
    )

    near_count = sum(
        1 for c in chains
        if c.p2 is not None and c.confidence == "NEAR"
    )

    pending_count = sum(
        1 for c in chains
        if c.p2 is None
    )

    lines.extend(
        [
            "",
            "SUMMARY",
            "-" * 100,
            f"Total 42->21 candidates : {len(chains)}",
            f"Exact completed chains  : {exact_count}",
            f"Near completed chains   : {near_count}",
            f"42-leg only / pending   : {pending_count}",
            "",
            "NEXT PROJECTIONS",
            "-" * 100,
        ]
    )

    if not projections:
        lines.append("No next projection available.")

    for p in projections:
        target = future_time_from_bar_index(
            df,
            p["target_index"],
        )

        target_text = (
            target.strftime("%d-%b-%Y %H:%M")
            if target
            else "N/A"
        )

        lines.append(
            f"{p['reason']:>20} | "
            f"Origin : {p['source'].kind} "
            f"{p['source'].time:%d-%b-%Y %H:%M}"
            f" | +{p['bars']} bars"
            f" | Target : {p['target_kind']} {target_text}"
        )

    REPORT_FILE.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        print("\n[Telegram not configured]\n" + message)
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    response = requests.post(
        url,
        data={
            "chat_id": chat_id,
            "text": message,
        },
        timeout=10,
    )

    response.raise_for_status()


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}

    try:
        return json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(
            state,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


def emit_alert_once(
    key: str,
    message: str,
    state: dict,
) -> bool:

    if state.get(key):
        return False

    telegram_send(message)

    state[key] = datetime.now().isoformat()

    save_state(state)

    return True


# ============================================================
# LIVE RUN
# ============================================================

def run_once(days_back: int) -> None:
    today = datetime.now().date()
    start_date = today - timedelta(
        days=days_back
    )

    print(
        f"Config: ALERT_MODE={ALERT_MODE}, "
        f"CYCLE_TOLERANCE={CYCLE_TOLERANCE}, "
        f"PIVOT_STRENGTH={PIVOT_STRENGTH}"
    )

    df = fetch_history(
        start_date.isoformat(),
        today.isoformat(),
    )

    pivots = detect_pivots(df)

    chains = find_42_21_chains(
        pivots,
        CYCLE_TOLERANCE,
    )

    # Use the most recent chain for forward-cycle work.
    latest_chain = chains[-1] if chains else None

    projections = (
        project_from_chain(latest_chain)
        if latest_chain
        else []
    )

    convergence = find_convergence(
        df,
        projections,
    )

    latest = df.iloc[-1]
    latest_time = latest["datetime"].to_pydatetime()

    running_pivot = (
        pivots[-1]
        if pivots
        else None
    )

    if running_pivot:
        running_row = df.iloc[
            running_pivot.index
        ]
        running_time = running_pivot.time
    else:
        running_row = latest
        running_time = latest_time

    # TPS uses the latest completed FYERS candle.
    tps = time_price_square(
        df,
        latest_time,
    )

    mother = bool(convergence)

    if mother and tps.get("square"):
        status = "Mother Candle + TPS"
    elif mother:
        status = "Mother Candle"
    elif tps.get("square"):
        status = "Time Price Square"
    else:
        status = "Normal Cycle"

    # -------------------------
    # Simple live output
    # -------------------------

    print(
        "\n".join(
            [
                "NIFTY 5-Minute Advanced Cycle Bot",
                "=" * 60,
                "Fetching historical candles...",
                f"Fetched {len(df)} candles.",
                "",
                "Running Cycle",
                "-" * 40,
                f"Date : {running_time:%Y-%m-%d %H:%M}",
                f"High : {float(running_row['high']):.2f}",
                f"Low  : {float(running_row['low']):.2f}",
                "",
                "Cycle Status",
                "-" * 40,
                f"Status : {status}",
            ]
        )
    )

    # -------------------------
    # Alert policy
    # -------------------------

    if ALERT_MODE == "GENERAL":
        should_alert = True

    elif ALERT_MODE == "SPECIAL":
        should_alert = status in {
            "Mother Candle",
            "Time Price Square",
            "Mother Candle + TPS",
        }

    elif ALERT_MODE == "MOTHER":
        should_alert = status in {
            "Mother Candle",
            "Mother Candle + TPS",
        }

    elif ALERT_MODE == "TPS":
        should_alert = status in {
            "Time Price Square",
            "Mother Candle + TPS",
        }

    else:
        raise ValueError(
            "ALERT_MODE must be one of: "
            "GENERAL, SPECIAL, MOTHER, TPS"
        )

    telegram_message = (
        "NIFTY 5-Minute Advanced Cycle Bot\n"
        "----------------------------------------\n"
        "Running Cycle\n"
        f"Date : {running_time:%Y-%m-%d %H:%M}\n"
        f"High : {float(running_row['high']):.2f}\n"
        f"Low  : {float(running_row['low']):.2f}\n\n"
        f"Status : {status}"
    )

    state = load_state()

    if should_alert:
        alert_key = (
            f"RUNNING:"
            f"{running_time.isoformat()}:"
            f"{status}"
        )

        sent = emit_alert_once(
            alert_key,
            telegram_message,
            state,
        )

        if sent:
            print(
                "Telegram message sent successfully."
            )
            print(
                "Telegram alert sent for cycle : "
                f"{running_time:%Y-%m-%d %H:%M}"
            )
        else:
            print(
                "Telegram alert already sent "
                "for this cycle/status."
            )

    else:
        print(
            "Telegram alert skipped "
            f"(ALERT_MODE={ALERT_MODE})."
        )

    # -------------------------
    # Backend details
    # -------------------------

    write_backend_report(
        df=df,
        pivots=pivots,
        chains=chains,
        projections=projections,
        status=status,
    )

    print(
        f"Report: {REPORT_FILE}"
    )


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="NIFTY 5m 42->21 Advanced Cycle Bot"
    )

    parser.add_argument(
        "--days-back",
        type=int,
        default=30,
        help="Historical calendar days (default: 30)",
    )

    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously and refresh every 60 seconds",
    )

    args = parser.parse_args()

    if not args.loop:
        run_once(args.days_back)
        return

    print(
        "NIFTY 5-Minute Advanced Cycle Bot Started..."
    )

    while True:
        try:
            run_once(args.days_back)
        except Exception as exc:
            print(f"[ERROR] {exc}")

        time_module.sleep(60)


if __name__ == "__main__":
    main()
