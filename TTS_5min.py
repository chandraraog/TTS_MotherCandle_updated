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

# ------------------------------------------------------------------
# ORIGIN POINTS (A -> B -> C)
#
#   A --42--> B --21--> C    (and A --63--> C as the composite cycle)
#
# Set MANUAL_ORIGIN to pin the bot to a specific confirmed chain.
# Format: "%Y-%m-%d %H:%M", exactly matching a detected pivot time.
# Set MANUAL_ORIGIN = None to auto-select the most recent EXACT
# 42->21 chain instead.
# ------------------------------------------------------------------
MANUAL_ORIGIN: Optional[tuple[str, str, str]] = (
    "2026-07-29 14:55",  # A (HIGH)
    "2026-07-30 12:10",  # B (LOW)
    "2026-07-30 13:55",  # C (HIGH)
)

# How many future repeats of each cycle length (AB, BC, AC) to project.
CYCLE_FORWARD_COUNT = 60

# Bar-matching window used when deciding whether two/three projected
# cycle lines "land on the same candle" (Convergence / Mother Candle),
# and when checking whether the running candle is due for a projected
# cycle line.
CONVERGENCE_TOLERANCE_BARS = 1

# Time Price Square: how many of the most recent already-occurred
# cyclic lines to check, and how many of those must overlap the
# running candle's price range for it to count as a TPS.
TPS_LOOKBACK_LINES = 5
TPS_MIN_OVERLAP = 3

# Telegram alert control:
# GENERAL     -> alert on every run, regardless of status
# SPECIAL     -> alert only Convergence / Mother Candle / TPS combos
# MOTHER      -> alert only Mother Candle (with or without TPS)
# CONVERGENCE -> alert only Convergence (with or without TPS)
# TPS         -> alert only when the TPS condition is true
ALERT_MODE = "GENERAL"

REPORT_FILE = Path(__file__).with_name("advanced_cycle_5m_report.txt")
STATE_FILE = Path(__file__).with_name("advanced_cycle_5m_state.json")
LAST_ALERT_FILE = Path(__file__).with_name("last_alert.txt")

# Memory tracker to suppress duplicate console prints
_last_printed_console_key: str = ""


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


@dataclass(frozen=True)
class CycleOrigin:
    a: Pivot
    b: Pivot
    c: Pivot
    len_ab: int
    len_bc: int
    len_ac: int
    manual: bool


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
    if strength < 1:
        raise ValueError(f"PIVOT_STRENGTH must be >= 1, got {strength}.")

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

        if (
            left_high.size == 0
            or right_high.size == 0
            or left_low.size == 0
            or right_low.size == 0
        ):
            continue

        h = float(highs[i])
        l = float(lows[i])

        is_high = h > left_high.max() and h > right_high.max()
        is_low = l < left_low.min() and l < right_low.min()

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
# ORIGIN (A -> B -> C) RESOLUTION
# ============================================================

def find_pivot_by_time(
    pivots: list[Pivot],
    target: datetime,
) -> Optional[Pivot]:
    for p in pivots:
        if p.time == target:
            return p
    return None


def resolve_manual_origin(pivots: list[Pivot]) -> Optional[CycleOrigin]:
    if not MANUAL_ORIGIN:
        return None

    a_str, b_str, c_str = MANUAL_ORIGIN

    a = find_pivot_by_time(pivots, datetime.strptime(a_str, "%Y-%m-%d %H:%M"))
    b = find_pivot_by_time(pivots, datetime.strptime(b_str, "%Y-%m-%d %H:%M"))
    c = find_pivot_by_time(pivots, datetime.strptime(c_str, "%Y-%m-%d %H:%M"))

    if not (a and b and c):
        return None

    return CycleOrigin(
        a=a,
        b=b,
        c=c,
        len_ab=b.index - a.index,
        len_bc=c.index - b.index,
        len_ac=c.index - a.index,
        manual=True,
    )


def pick_origin(
    pivots: list[Pivot],
    chains: list[CycleChain],
) -> Optional[CycleOrigin]:
    manual = resolve_manual_origin(pivots)
    if manual:
        return manual

    for chain in reversed(chains):
        if chain.confidence == "EXACT" and chain.p2 is not None:
            return CycleOrigin(
                a=chain.p0,
                b=chain.p1,
                c=chain.p2,
                len_ab=chain.p1.index - chain.p0.index,
                len_bc=chain.p2.index - chain.p1.index,
                len_ac=chain.p2.index - chain.p0.index,
                manual=False,
            )

    for chain in reversed(chains):
        if chain.p2 is not None:
            return CycleOrigin(
                a=chain.p0,
                b=chain.p1,
                c=chain.p2,
                len_ab=chain.p1.index - chain.p0.index,
                len_bc=chain.p2.index - chain.p1.index,
                len_ac=chain.p2.index - chain.p0.index,
                manual=False,
            )

    return None


# ============================================================
# CYCLE SERIES PROJECTION (AB / BC / AC)
# ============================================================

def build_cycle_series(
    origin: CycleOrigin,
    forward: int = CYCLE_FORWARD_COUNT,
) -> dict[str, list[int]]:
    ab = [origin.a.index + n * origin.len_ab for n in range(forward + 1)]
    bc = [origin.b.index + n * origin.len_bc for n in range(forward + 1)]
    ac = [origin.a.index + n * origin.len_ac for n in range(forward + 1)]

    return {"AB": ab, "BC": bc, "AC": ac}


def classify_cycle_events(
    series: dict[str, list[int]],
    tolerance: int = CONVERGENCE_TOLERANCE_BARS,
) -> list[dict]:
    points: list[tuple[int, str]] = []
    for tag, idxs in series.items():
        for idx in idxs:
            points.append((idx, tag))

    points.sort(key=lambda p: p[0])

    clusters: list[dict] = []
    for idx, tag in points:
        if clusters and abs(idx - clusters[-1]["index"]) <= tolerance:
            clusters[-1]["indices"].append(idx)
            clusters[-1]["tags"].add(tag)
        else:
            clusters.append(
                {
                    "index": idx,
                    "indices": [idx],
                    "tags": {tag},
                }
            )

    events: list[dict] = []
    for cl in clusters:
        count = len(cl["tags"])
        if count >= 3:
            kind = "Mother Candle - 3 Lines"
        elif count == 2:
            kind = "Convergence - 2 Lines"
        else:
            kind = "Normal Cycle (Single Line)"

        events.append(
            {
                "index": cl["index"],
                "count": count,
                "tags": sorted(cl["tags"]),
                "kind": kind,
            }
        )

    return events


def find_due_events(
    events: list[dict],
    current_index: int,
    tolerance: int = CONVERGENCE_TOLERANCE_BARS,
) -> list[dict]:
    return [
        e for e in events
        if abs(e["index"] - current_index) <= tolerance
    ]


# ============================================================
# FUTURE PROJECTION
# ============================================================

def future_time_from_bar_index(
    df: pd.DataFrame,
    target_index: int,
) -> Optional[datetime]:
    if target_index < 0:
        return None

    if target_index < len(df):
        return df.iloc[target_index]["datetime"].to_pydatetime()

    last_time = df.iloc[-1]["datetime"].to_pydatetime()
    extra_bars = target_index - (len(df) - 1)

    session_start = last_time.replace(hour=9, minute=15, second=0, microsecond=0)
    current_slot = int((last_time - session_start).total_seconds() // 300)

    target_date = last_time.date()
    target_slot = current_slot

    while extra_bars > 0:
        target_slot += 1
        if target_slot >= 75:  # 75 5m candles/day (09:15 to 15:25)
            target_date += timedelta(days=1)
            while target_date.weekday() >= 5:  # Skip weekends
                target_date += timedelta(days=1)
            target_slot = 0
        extra_bars -= 1

    return datetime.combine(
        target_date,
        datetime.min.time(),
    ).replace(hour=9, minute=15) + timedelta(minutes=5 * target_slot)


def project_from_chain(
    chain: CycleChain,
) -> list[dict]:
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
# TIME PRICE SQUARE
# ============================================================

def time_price_square(
    df: pd.DataFrame,
    series: dict[str, list[int]],
    current_index: int,
    lookback_lines: int = TPS_LOOKBACK_LINES,
    min_overlap: int = TPS_MIN_OVERLAP,
) -> dict:
    if current_index < 0 or current_index >= len(df):
        return {"available": False, "square": False}

    past_indices = sorted(
        {
            idx
            for idxs in series.values()
            for idx in idxs
            if 0 <= idx < current_index
        },
        reverse=True,
    )[:lookback_lines]

    if len(past_indices) < 3:
        return {
            "available": False,
            "square": False,
            "checked_lines": len(past_indices),
        }

    current_row = df.iloc[current_index]
    current_high = float(current_row["high"])
    current_low = float(current_row["low"])

    overlap_count = 0
    zone_low = float("inf")
    zone_high = float("-inf")

    for idx in past_indices:
        row = df.iloc[idx]
        p_high = float(row["high"])
        p_low = float(row["low"])

        if current_high >= p_low and current_low <= p_high:
            overlap_count += 1

        zone_low = min(zone_low, p_low)
        zone_high = max(zone_high, p_high)

    square = overlap_count >= min_overlap

    return {
        "available": True,
        "square": square,
        "overlap_count": overlap_count,
        "checked_lines": len(past_indices),
        "zone_low": zone_low,
        "zone_high": zone_high,
        "current_high": current_high,
        "current_low": current_low,
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
    origin: Optional[CycleOrigin] = None,
    events: Optional[list[dict]] = None,
    current_index: Optional[int] = None,
    tps: Optional[dict] = None,
) -> None:

    lines = [
        "=" * 100,
        f"ADVANCED CYCLE - BACKEND DETAILS ({SYMBOL})",
        "=" * 100,
        f"Symbol              : {SYMBOL}",
        f"FYERS candles       : {len(df)}",
        f"Last candle         : {df.iloc[-1]['datetime']:%d-%b-%Y %H:%M}",
        f"Pivot count         : {len(pivots)}",
        f"Cycle tolerance     : +/- {CYCLE_TOLERANCE} bars",
        f"Current status      : {status}",
        "",
    ]

    if origin:
        lines.extend(
            [
                "ORIGIN (A -> B -> C)",
                "-" * 100,
                f"Source : {'MANUAL_ORIGIN' if origin.manual else 'Auto (latest EXACT chain)'}",
                f"A : {origin.a.kind} {origin.a.time:%d-%b-%Y %H:%M}",
                f"B : {origin.b.kind} {origin.b.time:%d-%b-%Y %H:%M}"
                f"  (A->B = {origin.len_ab} bars)",
                f"C : {origin.c.kind} {origin.c.time:%d-%b-%Y %H:%M}"
                f"  (B->C = {origin.len_bc} bars, A->C = {origin.len_ac} bars)",
                "",
            ]
        )
    else:
        lines.extend(["ORIGIN (A -> B -> C)", "-" * 100, "No origin resolved.", ""])

    lines.extend(["RECENT 42 -> 21 CHAINS", "-" * 100])

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

    lines.extend(
        [
            "",
            "UPCOMING CONVERGENCE / MOTHER CANDLE (next 15)",
            "-" * 100,
        ]
    )

    if events and current_index is not None:
        upcoming = sorted(
            (e for e in events if e["index"] > current_index),
            key=lambda e: e["index"],
        )[:15]

        if not upcoming:
            lines.append("No upcoming events within the projected window.")

        for e in upcoming:
            target = future_time_from_bar_index(df, e["index"])
            target_text = (
                target.strftime("%d-%b-%Y %H:%M") if target else "N/A"
            )
            lines.append(
                f"{e['kind']:>35} | lines={'+'.join(e['tags']):<8} | "
                f"bar {e['index']:>6} | {target_text}"
            )
    else:
        lines.append("No cycle events available (origin not resolved).")

    REPORT_FILE.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


# ============================================================
# TELEGRAM DEDUPLICATION (last_alert.txt)
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


def get_last_alert_key() -> str:
    if not LAST_ALERT_FILE.exists():
        return ""
    try:
        return LAST_ALERT_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def set_last_alert_key(alert_key: str) -> None:
    try:
        LAST_ALERT_FILE.write_text(alert_key.strip(), encoding="utf-8")
    except Exception as exc:
        print(f"[Warning] Could not write to last_alert.txt: {exc}")


def emit_alert_if_new(
    alert_key: str,
    message: str,
) -> bool:
    last_key = get_last_alert_key()

    if last_key == alert_key:
        return False

    telegram_send(message)
    set_last_alert_key(alert_key)
    return True


# ============================================================
# LIVE RUN
# ============================================================

def run_once(days_back: int) -> None:
    global _last_printed_console_key

    today = datetime.now().date()
    start_date = today - timedelta(days=days_back)

    df = fetch_history(
        start_date.isoformat(),
        today.isoformat(),
    )

    pivots = detect_pivots(df)
    chains = find_42_21_chains(pivots, CYCLE_TOLERANCE)
    origin = pick_origin(pivots, chains)

    latest_chain = chains[-1] if chains else None
    projections = (
        project_from_chain(latest_chain)
        if latest_chain
        else []
    )

    series: dict[str, list[int]] = {}
    events: list[dict] = []

    if origin:
        series = build_cycle_series(origin, CYCLE_FORWARD_COUNT)
        events = classify_cycle_events(series, CONVERGENCE_TOLERANCE_BARS)

    current_index = len(df) - 1

    due_events = find_due_events(events, current_index, CONVERGENCE_TOLERANCE_BARS)
    mother_due = any("Mother Candle" in e["kind"] for e in due_events)
    convergence_due = any("Convergence" in e["kind"] for e in due_events)

    # Calculate TPS whenever ANY active cycle line is present
    tps: dict = {"available": False, "square": False}
    if due_events and series:
        tps = time_price_square(
            df,
            series,
            current_index,
            TPS_LOOKBACK_LINES,
            TPS_MIN_OVERLAP,
        )

    is_tps = tps.get("square", False)

    # Classify overall status
    if mother_due and is_tps:
        status = "Mother Candle + TPS"
    elif mother_due:
        status = "Mother Candle"
    elif convergence_due and is_tps:
        status = "Convergence + TPS"
    elif convergence_due:
        status = "Convergence"
    elif is_tps:
        status = "Normal Cycle + TPS"
    else:
        status = "Normal Cycle"

    # Extract Active Cycle Details
    active_time_str = "N/A"
    active_high = 0.0
    active_low = 0.0
    active_type = "Normal Cycle (Single Line)"
    next_cycle_str = "N/A"
    current_cycle_key = ""

    if events and current_index is not None:
        past = [e for e in events if e["index"] <= current_index]
        if past:
            last_e = past[-1]
            last_dt = future_time_from_bar_index(df, last_e["index"])
            if last_dt:
                active_time_str = last_dt.strftime("%Y-%m-%d %H:%M")
                current_cycle_key = active_time_str

            active_type = last_e["kind"]

            if 0 <= last_e["index"] < len(df):
                row = df.iloc[last_e["index"]]
                active_high = float(row["high"])
                active_low = float(row["low"])

        future = [e for e in events if e["index"] > current_index]
        if future:
            next_e = future[0]
            next_dt = future_time_from_bar_index(df, next_e["index"])
            next_time_str = next_dt.strftime("%Y-%m-%d %H:%M") if next_dt else "N/A"
            next_cycle_str = f"{next_time_str} ({next_e['kind']})"

    # Origin details
    a_txt = f"{origin.a.time:%d-%b %H:%M}" if origin else "N/A"
    b_txt = f"{origin.b.time:%d-%b %H:%M}" if origin else "N/A"
    c_txt = f"{origin.c.time:%d-%b %H:%M}" if origin else "N/A"

    now_str = datetime.now().strftime("%H:%M:%S")

    # STRICT ALERT CONDITION: Only Mother Candle OR TPS triggers alert & console print
    should_notify = mother_due or is_tps

    if should_notify and current_cycle_key:
        if current_cycle_key != _last_printed_console_key:
            _last_printed_console_key = current_cycle_key

            console_output = (
                f"\n🚨 5m Cycle Alert ({SYMBOL})\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                f"• Symbol : {SYMBOL}\n"
                "📌 Origin (A → B → C)\n"
                f"• A (HIGH) : {a_txt}\n"
                f"• B (LOW)  : {b_txt}\n"
                f"• C (HIGH) : {c_txt}\n\n"
                "📊 Active Cycle Line\n"
                f"• Symbol : {SYMBOL}\n"
                f"• Time   : {active_time_str}\n"
                f"• High   : {active_high:.2f}\n"
                f"• Low    : {active_low:.2f}\n"
                f"• Type   : {active_type}\n\n"
                "🎯 Next Upcoming Cycle\n"
                f"• Target : {next_cycle_str}\n\n"
                f"⚡ Status: {status}\n"
            )
            print(console_output)

        # Telegram Alert Message
        telegram_alert_msg = (
            "📊 Active Cycle Line\n"
            f"• Symbol : {SYMBOL}\n"
            f"• Time   : {active_time_str}\n"
            f"• High   : {active_high:.2f}\n"
            f"• Low    : {active_low:.2f}\n"
            f"• Type   : {active_type}\n"
            f"• Status : {status}"
        )

        sent = emit_alert_if_new(current_cycle_key, telegram_alert_msg)
        if sent:
            print(f"[Telegram] Alert sent for cycle: {current_cycle_key}")
    else:
        # Ignore normal cycles & 2-line convergences (Heartbeat only)
        print(
            f"[{now_str}] Heartbeat: Checking candles... (Skipped: {status})",
            end="\r",
        )

    write_backend_report(
        df=df,
        pivots=pivots,
        chains=chains,
        projections=projections,
        status=status,
        origin=origin,
        events=events,
        current_index=current_index,
        tps=tps,
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
        help="Run continuously every 5 minutes (300s)",
    )

    args = parser.parse_args()

    if not args.loop:
        run_once(args.days_back)
        return

    print("NIFTY 5-Minute Advanced Cycle Bot Started...")

    while True:
        try:
            run_once(args.days_back)
        except Exception as exc:
            print(f"\n[ERROR] {exc}")

        # Sleep for 5 minutes (300 seconds) between checks
        time_module.sleep(300)

def load_stock_origin(symbol: str, resolution: str) -> Optional[tuple[str, str, str]]:
    config_path = Path(__file__).with_name("config.json")
    if not config_path.exists():
        return None
    
    config = json.loads(config_path.read_text(encoding="utf-8"))
    stock_cfg = config.get(symbol, {}).get("timeframes", {}).get(resolution, {})
    
    a_str = stock_cfg.get("A")
    b_str = stock_cfg.get("B")
    c_str = stock_cfg.get("C")
    
    if a_str and b_str and c_str:
        return (a_str, b_str, c_str)
    
    return None

if __name__ == "__main__":
    main()