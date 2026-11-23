"""
Advanced Cycle / Timing-Candle Research

Purpose
-------
Reverse-engineer the published NIFTY 5-minute timing candles by comparing them
with real NIFTY 5-minute OHLC data from FYERS.

This is a research tool only. It does NOT change the existing cycle bot.

What it checks:
1. Whether each published timing is near an actual pivot.
2. Distance in bars from the previous meaningful pivot.
3. Repeated pivot-to-pivot cycle lengths.
4. Equal cycles such as A -- N bars -- B -- N bars -- C.
5. Candidate bar lengths ranked by how often they align with published timings.

No database is required.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd

from fyers_client import get_fyers


# ==========================================================
# SETTINGS
# ==========================================================

SYMBOL = "NSE:NIFTY50-INDEX"
RESOLUTION = "5"
RANGE_FROM = "2026-07-01"
RANGE_TO = "2026-07-30"

TIMING_FILE = Path("timing.txt")
REPORT_FILE = Path("timing_analysis_report.txt")
CSV_FILE = Path("timing_analysis_rows.csv")

# Search space for the cycle length we are trying to discover.
MIN_CYCLE_BARS = 5
MAX_CYCLE_BARS = 60

# A published timing is a 5-minute window. Give the market pivot a small
# tolerance because a timing candle can represent a window rather than an
# exact turning tick.
TIMING_TOLERANCE_BARS = 1

# Pivot strengths to test. Higher strength = more significant pivot.
PIVOT_STRENGTHS = (2, 3, 5)


# ==========================================================
# PUBLISHED TIMINGS
# ==========================================================

@dataclass(frozen=True)
class PublishedTiming:
    date: str
    time: str

    @property
    def dt(self) -> datetime:
        return datetime.strptime(
            f"{self.date} {self.time}",
            "%Y-%m-%d %H:%M",
        )


def _normalize_time_token(token: str) -> str:
    token = token.strip().lower().replace(".", ":")
    token = token.replace(" ", "")

    # Accept forms such as 1, 1.10, 1.15, 10, 11.05, 12.50.
    if ":" not in token:
        hour = int(token)
        minute = 0
    else:
        hour_s, minute_s = token.split(":", 1)
        hour = int(hour_s)
        minute = int(minute_s)

    if hour < 9:
        hour += 12

    return f"{hour:02d}:{minute:02d}"


def load_published_timings(path: Path) -> list[PublishedTiming]:
    """Parse the existing timing.txt file."""

    if not path.exists():
        raise FileNotFoundError(f"Timing file not found: {path.resolve()}")

    rows: list[PublishedTiming] = []
    current_date: str | None = None

    # Example accepted lines:
    # NIFTY: 12.50 to 12.55
    # NIFTY: 9.50 to 9.55 & 1.55 to 2
    # NIFTY: 10 to 10.05, 11.25 to 11.30
    import re

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue

        date_match = re.fullmatch(
            r"(\d{1,2})-(January|February|March|April|May|June|July|August|September|October|November|December)-(\d{4})",
            line,
            re.IGNORECASE,
        )
        if date_match:
            day = int(date_match.group(1))
            month_name = date_match.group(2).title()
            year = int(date_match.group(3))
            month = datetime.strptime(month_name, "%B").month
            current_date = f"{year:04d}-{month:02d}-{day:02d}"
            continue

        if not current_date or not line.upper().startswith("NIFTY:"):
            continue

        payload = line.split(":", 1)[1]
        # Split multiple timing ranges.
        ranges = re.split(r"\s*(?:,|&)\s*", payload)

        for item in ranges:
            match = re.search(r"([0-9]{1,2}(?:[.:][0-9]{1,2})?)\s+to\s+([0-9]{1,2}(?:[.:][0-9]{1,2})?)", item, re.I)
            if not match:
                continue

            start_time = _normalize_time_token(match.group(1))
            rows.append(PublishedTiming(current_date, start_time))

    rows.sort(key=lambda x: x.dt)
    return rows


# ==========================================================
# FYERS DATA
# ==========================================================

def fetch_nifty_5m() -> list[list[float]]:
    """Fetch NIFTY 5-minute candles from FYERS using the existing client."""

    fyers = get_fyers()

    request = {
        "symbol": SYMBOL,
        "resolution": RESOLUTION,
        "date_format": "1",
        "range_from": RANGE_FROM,
        "range_to": RANGE_TO,
        "cont_flag": "1",
    }

    print("Fetching NIFTY 5-minute candles from FYERS...")
    response = fyers.history(request)

    if response.get("s") != "ok" or "candles" not in response:
        raise RuntimeError(f"FYERS Error: {response}")

    candles = response["candles"]
    if not candles:
        raise RuntimeError("FYERS returned no NIFTY candles.")

    print(f"Fetched {len(candles)} candles.")
    return candles


def candles_to_df(candles: list[list[float]]) -> pd.DataFrame:
    df = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )

    df["datetime"] = (
        pd.to_datetime(df["timestamp"], unit="s", utc=True)
        .dt.tz_convert("Asia/Kolkata")
        .dt.tz_localize(None)
    )

    df = df.sort_values("datetime").reset_index(drop=True)

    # Defensive cleanup: retain regular market candles only.
    df = df[
        (df["datetime"].dt.weekday < 5)
        & (df["datetime"].dt.time >= datetime.strptime("09:15", "%H:%M").time())
        & (df["datetime"].dt.time <= datetime.strptime("15:25", "%H:%M").time())
    ].reset_index(drop=True)

    return df


# ==========================================================
# PIVOTS
# ==========================================================

def detect_pivots(df: pd.DataFrame, strength: int) -> list[dict]:
    """Detect strict local pivot highs/lows."""

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    pivots: list[dict] = []

    for i in range(strength, len(df) - strength):
        left_high = highs[i - strength:i]
        right_high = highs[i + 1:i + strength + 1]
        left_low = lows[i - strength:i]
        right_low = lows[i + 1:i + strength + 1]

        if highs[i] > left_high.max() and highs[i] > right_high.max():
            pivots.append({
                "index": i,
                "datetime": df.iloc[i]["datetime"],
                "price": float(highs[i]),
                "type": "HIGH",
            })
        elif lows[i] < left_low.min() and lows[i] < right_low.min():
            pivots.append({
                "index": i,
                "datetime": df.iloc[i]["datetime"],
                "price": float(lows[i]),
                "type": "LOW",
            })

    # Remove consecutive same-type pivots, keeping the stronger one.
    cleaned: list[dict] = []
    for p in pivots:
        if not cleaned:
            cleaned.append(p)
            continue

        last = cleaned[-1]
        if p["type"] != last["type"]:
            cleaned.append(p)
        elif p["type"] == "HIGH" and p["price"] > last["price"]:
            cleaned[-1] = p
        elif p["type"] == "LOW" and p["price"] < last["price"]:
            cleaned[-1] = p

    return cleaned


# ==========================================================
# TIMING MATCHING
# ==========================================================

def find_candle_index(df: pd.DataFrame, dt: datetime) -> int | None:
    matches = df.index[df["datetime"] == dt]
    if len(matches) == 0:
        return None
    return int(matches[0])


def nearest_pivot(
    pivots: list[dict],
    target_index: int,
    tolerance: int,
) -> tuple[dict | None, int | None]:
    candidates = [
        p for p in pivots
        if abs(p["index"] - target_index) <= tolerance
    ]

    if not candidates:
        return None, None

    best = min(candidates, key=lambda p: abs(p["index"] - target_index))
    return best, abs(best["index"] - target_index)


def previous_pivot(pivots: list[dict], pivot_index: int) -> dict | None:
    previous = [p for p in pivots if p["index"] < pivot_index]
    return previous[-1] if previous else None


def analyze_timings(
    df: pd.DataFrame,
    timings: list[PublishedTiming],
    strength: int,
) -> pd.DataFrame:
    pivots = detect_pivots(df, strength)
    pivot_by_index = {p["index"]: p for p in pivots}

    rows: list[dict] = []

    for timing in timings:
        target = timing.dt
        idx = find_candle_index(df, target)

        row = {
            "date": timing.date,
            "published_time": timing.time,
            "candle_index": idx,
            "pivot_strength": strength,
            "published_is_pivot": False,
            "pivot_type": "",
            "pivot_time": "",
            "pivot_offset_bars": None,
            "previous_pivot_time": "",
            "previous_pivot_type": "",
            "previous_pivot_gap_bars": None,
            "next_pivot_gap_bars": None,
        }

        if idx is None:
            rows.append(row)
            continue

        exact = pivot_by_index.get(idx)
        if exact:
            row["published_is_pivot"] = True
            row["pivot_type"] = exact["type"]
            row["pivot_time"] = exact["datetime"].strftime("%H:%M")
            pivot = exact
        else:
            pivot, offset = nearest_pivot(
                pivots,
                idx,
                TIMING_TOLERANCE_BARS,
            )
            if pivot:
                row["pivot_type"] = pivot["type"]
                row["pivot_time"] = pivot["datetime"].strftime("%H:%M")
                row["pivot_offset_bars"] = offset

        if pivot:
            prev = previous_pivot(pivots, pivot["index"])
            if prev:
                gap = pivot["index"] - prev["index"]
                row["previous_pivot_time"] = prev["datetime"].strftime("%H:%M")
                row["previous_pivot_type"] = prev["type"]
                row["previous_pivot_gap_bars"] = gap

            after = [p for p in pivots if p["index"] > pivot["index"]]
            if after:
                row["next_pivot_gap_bars"] = after[0]["index"] - pivot["index"]

        rows.append(row)

    return pd.DataFrame(rows)


# ==========================================================
# EQUAL PIVOT CYCLE ANALYSIS
# ==========================================================

def equal_cycle_counts(pivots: list[dict]) -> Counter:
    counts: Counter = Counter()
    for i in range(len(pivots) - 2):
        a = pivots[i]
        b = pivots[i + 1]
        c = pivots[i + 2]
        ab = b["index"] - a["index"]
        bc = c["index"] - b["index"]
        if ab == bc and MIN_CYCLE_BARS <= ab <= MAX_CYCLE_BARS:
            counts[ab] += 1
    return counts


def timing_alignment_scores(
    df: pd.DataFrame,
    timings: list[PublishedTiming],
    pivots: list[dict],
) -> dict[int, dict]:
    """
    Score each N-bar cycle by projecting a previous pivot N bars forward
    and checking whether the result lands on/near a published timing.
    """

    published_indices = []
    for timing in timings:
        idx = find_candle_index(df, timing.dt)
        if idx is not None:
            published_indices.append(idx)

    published_set = set(published_indices)
    scores: dict[int, dict] = {
        n: {
            "exact": 0,
            "near": 0,
            "examples": [],
        }
        for n in range(MIN_CYCLE_BARS, MAX_CYCLE_BARS + 1)
    }

    for pivot in pivots:
        for n in range(MIN_CYCLE_BARS, MAX_CYCLE_BARS + 1):
            target_idx = pivot["index"] + n
            if target_idx >= len(df):
                continue

            if target_idx in published_set:
                scores[n]["exact"] += 1
                scores[n]["examples"].append(
                    f'{pivot["datetime"]:%d-%b %H:%M} {pivot["type"]} + {n} = {df.iloc[target_idx]["datetime"]:%d-%b %H:%M}'
                )
                continue

            # Timing window tolerance: +/- 1 bar.
            nearby = [
                p for p in published_indices
                if abs(p - target_idx) <= TIMING_TOLERANCE_BARS
            ]
            if nearby:
                scores[n]["near"] += 1

    return scores


# ==========================================================
# REPORT
# ==========================================================

def write_report(
    timings: list[PublishedTiming],
    df: pd.DataFrame,
    analyses: dict[int, pd.DataFrame],
    alignment_scores: dict[int, dict],
    equal_cycles: dict[int, Counter],
) -> None:

    lines: list[str] = []
    lines.append("=" * 90)
    lines.append("NIFTY ADVANCED CYCLE / TIMING-CANDLE RESEARCH")
    lines.append("=" * 90)
    lines.append(f"Data        : {SYMBOL} / {RESOLUTION}-minute")
    lines.append(f"Date range  : {RANGE_FROM} to {RANGE_TO}")
    lines.append(f"Timings     : {len(timings)} published timing candles")
    lines.append(f"Candles     : {len(df)}")
    lines.append("")

    for strength, result in analyses.items():
        lines.append("-" * 90)
        lines.append(f"PIVOT STRENGTH = {strength}")
        lines.append("-" * 90)

        valid = result[result["candle_index"].notna()]
        pivot_hits = int(valid["published_is_pivot"].sum())
        near_hits = int(valid["pivot_time"].astype(bool).sum())

        lines.append(f"Published candles found : {len(valid)}/{len(result)}")
        lines.append(f"Exact pivot matches      : {pivot_hits}")
        lines.append(f"Near pivot matches       : {near_hits}")
        lines.append("")

        lines.append("Date       Published  Pivot  PrevGap  PrevPivot")
        lines.append("-" * 55)
        for _, r in result.iterrows():
            prev_gap = "-" if pd.isna(r["previous_pivot_gap_bars"]) else str(int(r["previous_pivot_gap_bars"]))
            pivot = r["pivot_type"] or "-"
            prev_type = r["previous_pivot_type"] or "-"
            lines.append(
                f"{r['date']}  {r['published_time']}    {pivot:<4}  {prev_gap:>6}    {prev_type}"
            )
        lines.append("")

    # Ranking across the requested pivot strength values.
    combined: list[tuple[int, int, int, int]] = []
    for n, s in alignment_scores.items():
        exact = s["exact"]
        near = s["near"]
        equal = sum(eq.get(n, 0) for eq in equal_cycles.values())
        weighted = exact * 5 + near * 2 + equal
        combined.append((weighted, exact, near, n))

    combined.sort(reverse=True)

    lines.append("=" * 90)
    lines.append("CANDIDATE BAR-LENGTH RANKING")
    lines.append("=" * 90)
    lines.append("Bars  Score  Exact  Near  EqualPivotCycles")
    lines.append("-" * 55)

    for weighted, exact, near, n in combined[:15]:
        equal = sum(eq.get(n, 0) for eq in equal_cycles.values())
        lines.append(f"{n:4d}  {weighted:5d}  {exact:5d}  {near:4d}  {equal:16d}")

    lines.append("")
    lines.append("Interpretation:")
    lines.append("- Exact/near alignment means a previous pivot projected N bars forward lands on a published timing.")
    lines.append("- EqualPivotCycles means A -> B -> C where A-B and B-C are both N bars apart.")
    lines.append("- This is evidence for candidate cycle lengths, not proof of the publisher's formula.")

    lines.append("")
    lines.append("Top examples:")
    for weighted, exact, near, n in combined[:10]:
        if exact == 0:
            continue
        lines.append(f"\n{n} bars:")
        examples = alignment_scores[n]["examples"][:8]
        lines.extend(f"  {x}" for x in examples)

    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")


# ==========================================================
# MAIN
# ==========================================================

def main() -> None:
    print("=" * 80)
    print("NIFTY ADVANCED CYCLE / TIMING-CANDLE RESEARCH")
    print("=" * 80)

    timings = load_published_timings(TIMING_FILE)
    print(f"Published timing candles : {len(timings)}")

    candles = fetch_nifty_5m()
    df = candles_to_df(candles)
    print(f"Usable NIFTY 5-minute candles : {len(df)}")

    analyses: dict[int, pd.DataFrame] = {}
    pivot_sets: dict[int, list[dict]] = {}

    for strength in PIVOT_STRENGTHS:
        pivots = detect_pivots(df, strength)
        pivot_sets[strength] = pivots
        analyses[strength] = analyze_timings(df, timings, strength)
        print(f"Pivot strength {strength}: {len(pivots)} cleaned pivots")

    # Use the strongest pivot set for the primary timing-alignment ranking.
    primary_pivots = pivot_sets[max(PIVOT_STRENGTHS)]
    alignment = timing_alignment_scores(df, timings, primary_pivots)
    equal_cycles = {
        strength: equal_cycle_counts(pivots)
        for strength, pivots in pivot_sets.items()
    }

    write_report(timings, df, analyses, alignment, equal_cycles)

    # Create one CSV containing the strongest pivot analysis per published timing.
    analyses[max(PIVOT_STRENGTHS)].to_csv(CSV_FILE, index=False)

    print("\nResearch complete.")
    print(f"Report : {REPORT_FILE.resolve()}")
    print(f"CSV    : {CSV_FILE.resolve()}")
    print("\nOpen the report and look first at CANDIDATE BAR-LENGTH RANKING.")


if __name__ == "__main__":
    main()
