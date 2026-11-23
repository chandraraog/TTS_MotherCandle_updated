from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from fyers_client import get_fyers

# ============================================================
# CONFIGURATION & HISTORICAL TIME DATA
# ============================================================

SYMBOL = "NSE:NIFTY50-INDEX"
RESOLUTION = "5"
IST = "Asia/Kolkata"

# Target cycle lengths
TARGET_AB = 42
TARGET_BC = 21
TARGET_AC = 63

# Matching tolerance (0 = exact match only, 1 = +/- 1 bar offset)
TOLERANCE = 1

REPORT_FILE = Path(__file__).with_name("cycle_pattern_analysis_report.txt")

# User-provided list of cycle time candles
HISTORICAL_CYCLE_INPUTS = [
    {"date": "2026-07-30", "times": ["12:50"]},
    {"date": "2026-07-29", "times": ["10:20"]},
    {"date": "2026-07-28", "times": ["12:20"]},
    {"date": "2026-07-27", "times": ["11:15"]},
    {"date": "2026-07-23", "times": ["09:50"]},
    {"date": "2026-07-22", "times": ["10:55"]},
    {"date": "2026-07-21", "times": ["10:50"]},
    {"date": "2026-07-20", "times": ["11:55"]},
    {"date": "2026-07-17", "times": ["09:20", "13:55"]},
    {"date": "2026-07-16", "times": ["11:00"]},
    {"date": "2026-07-15", "times": ["12:00"]},
    {"date": "2026-07-14", "times": ["11:40"]},
    {"date": "2026-07-13", "times": ["10:20"]},
    {"date": "2026-07-10", "times": ["10:00", "11:25"]},
    {"date": "2026-07-09", "times": ["13:10"]},
    {"date": "2026-07-08", "times": ["11:50"]},
    {"date": "2026-07-07", "times": ["10:10", "13:00"]},
    {"date": "2026-07-06", "times": ["10:45", "11:20", "12:50"]},
    {"date": "2026-07-02", "times": ["12:00"]},
    {"date": "2026-07-01", "times": ["11:00"]},
]


@dataclass(frozen=True)
class ObservedCycleCandle:
    datetime_obj: datetime
    bar_index: int
    open_price: float
    high_price: float
    low_price: float
    close_price: float


# ============================================================
# FYERS DATA RETRIEVAL & INDEXING
# ============================================================

def fetch_fyers_range(start_date: str, end_date: str) -> pd.DataFrame:
    """Fetches continuous 5m candles from FYERS API for the full date range."""
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


# ============================================================
# PATTERN MATCHING ENGINE (42 / 21 / 63)
# ============================================================

def analyze_cycle_matches(
    df: pd.DataFrame,
    observed_nodes: list[ObservedCycleCandle],
    tolerance: int = TOLERANCE,
) -> dict:
    """
    Checks all pairs and triplets of observed cycle timestamps against
    the 42 / 21 / 63 bar count ratios.
    """
    results = {
        "42_matches": [],
        "21_matches": [],
        "63_matches": [],
        "chains_42_21": [],
    }

    # Sort nodes by bar index
    nodes = sorted(observed_nodes, key=lambda x: x.bar_index)
    n = len(nodes)

    for i in range(n):
        node_a = nodes[i]

        for j in range(i + 1, n):
            node_b = nodes[j]
            bars_ab = node_b.bar_index - node_a.bar_index

            err_42 = bars_ab - TARGET_AB
            err_21 = bars_ab - TARGET_BC
            err_63 = bars_ab - TARGET_AC

            # Check single leg matches
            if abs(err_42) <= tolerance:
                results["42_matches"].append(
                    {"a": node_a, "b": node_b, "bars": bars_ab, "error": err_42}
                )

            if abs(err_21) <= tolerance:
                results["21_matches"].append(
                    {"a": node_a, "b": node_b, "bars": bars_ab, "error": err_21}
                )

            if abs(err_63) <= tolerance:
                results["63_matches"].append(
                    {"a": node_a, "b": node_b, "bars": bars_ab, "error": err_63}
                )

            # Check 3-point chain (A -> B -> C = 42 -> 21)
            for k in range(j + 1, n):
                node_c = nodes[k]
                bars_bc = node_c.bar_index - node_b.bar_index
                bars_ac = node_c.bar_index - node_a.bar_index

                err_bc = bars_bc - TARGET_BC
                err_ac = bars_ac - TARGET_AC

                if abs(err_42) <= tolerance and abs(err_bc) <= tolerance:
                    results["chains_42_21"].append(
                        {
                            "a": node_a,
                            "b": node_b,
                            "c": node_c,
                            "bars_ab": bars_ab,
                            "bars_bc": bars_bc,
                            "bars_ac": bars_ac,
                            "err_ab": err_42,
                            "err_bc": err_bc,
                            "err_ac": err_ac,
                        }
                    )

    return results


# ============================================================
# MAIN EXECUTOR & REPORT GENERATOR
# ============================================================

def run_analysis() -> None:
    print("=" * 70)
    print("NIFTY INTRADAY TIME CYCLE ANALYZER (42 - 21 - 63 PATTERN)")
    print("=" * 70)

    # 1. Fetch FYERS data covering the full historical date range
    start_date = "2026-07-01"
    end_date = "2026-07-31"

    print(f"Fetching FYERS historical 5m candles ({start_date} to {end_date})...")
    df = fetch_fyers_range(start_date, end_date)
    print(f"Fetched {len(df)} candles.")

    # 2. Map input cycle times to exact DataFrame bar indices
    observed_nodes: list[ObservedCycleCandle] = []
    missing_times = []

    for entry in HISTORICAL_CYCLE_INPUTS:
        date_str = entry["date"]
        for time_str in entry["times"]:
            target_dt_str = f"{date_str} {time_str}"
            target_dt = datetime.strptime(target_dt_str, "%Y-%m-%d %H:%M")

            matched_rows = df[df["datetime"] == target_dt]

            if not matched_rows.empty:
                idx = int(matched_rows.index[0])
                row = matched_rows.iloc[0]
                observed_nodes.append(
                    ObservedCycleCandle(
                        datetime_obj=target_dt,
                        bar_index=idx,
                        open_price=float(row["open"]),
                        high_price=float(row["high"]),
                        low_price=float(row["low"]),
                        close_price=float(row["close"]),
                    )
                )
            else:
                missing_times.append(target_dt_str)

    print(f"Mapped {len(observed_nodes)} cycle candles out of total provided.")
    if missing_times:
        print(f"Warning: {len(missing_times)} times not found in FYERS feed: {missing_times}")

    # 3. Analyze patterns
    analysis = analyze_cycle_matches(df, observed_nodes, tolerance=TOLERANCE)

    # 4. Generate report lines
    lines = [
        "=" * 100,
        "NIFTY 5m TIME CYCLE PATTERN MATCHING REPORT (42 / 21 / 63 THEORY)",
        "=" * 100,
        f"Analyzed Period    : {start_date} to {end_date}",
        f"Total Input Nodes  : {len(observed_nodes)}",
        f"Matching Tolerance : +/- {TOLERANCE} bar(s)",
        "",
        "SUMMARY OF MATCHES FOUND",
        "-" * 100,
        f"1. Full Completed Chains (A --42--> B --21--> C) : {len(analysis['chains_42_21'])}",
        f"2. Single 42-Bar Legs (AB)                     : {len(analysis['42_matches'])}",
        f"3. Single 21-Bar Legs (BC)                     : {len(analysis['21_matches'])}",
        f"4. Composite 63-Bar Legs (AC)                  : {len(analysis['63_matches'])}",
        "",
        "1. COMPLETED 42 -> 21 CYCLE CHAINS (A -> B -> C)",
        "-" * 100,
    ]

    if not analysis["chains_42_21"]:
        lines.append("No exact/near 42->21 completed chains detected in the provided list.")
    else:
        for idx, chain in enumerate(analysis["chains_42_21"], 1):
            a, b, c = chain["a"], chain["b"], chain["c"]
            lines.append(
                f"{idx:02d}. A: {a.datetime_obj:%d-%b %H:%M} [Bar {a.bar_index}] "
                f"--({chain['bars_ab']} bars, err:{chain['err_ab']:+d})--> "
                f"B: {b.datetime_obj:%d-%b %H:%M} [Bar {b.bar_index}] "
                f"--({chain['bars_bc']} bars, err:{chain['err_bc']:+d})--> "
                f"C: {c.datetime_obj:%d-%b %H:%M} [Bar {c.bar_index}] "
                f"| Composite AC = {chain['bars_ac']} bars"
            )

    lines.extend([
        "",
        "2. DETECTED 42-BAR LEGS (AB - FIRST CYCLE LEG)",
        "-" * 100,
    ])
    if not analysis["42_matches"]:
        lines.append("No 42-bar legs matched.")
    else:
        for idx, item in enumerate(analysis["42_matches"], 1):
            a, b = item["a"], item["b"]
            lines.append(
                f"{idx:02d}. {a.datetime_obj:%d-%b %H:%M} (Bar {a.bar_index:>4}) "
                f"--({item['bars']:>2} bars | err:{item['error']:+d})--> "
                f"{b.datetime_obj:%d-%b %H:%M} (Bar {b.bar_index:>4})"
            )

    lines.extend([
        "",
        "3. DETECTED 21-BAR LEGS (BC - SECOND CYCLE LEG)",
        "-" * 100,
    ])
    if not analysis["21_matches"]:
        lines.append("No 21-bar legs matched.")
    else:
        for idx, item in enumerate(analysis["21_matches"], 1):
            a, b = item["a"], item["b"]
            lines.append(
                f"{idx:02d}. {a.datetime_obj:%d-%b %H:%M} (Bar {a.bar_index:>4}) "
                f"--({item['bars']:>2} bars | err:{item['error']:+d})--> "
                f"{b.datetime_obj:%d-%b %H:%M} (Bar {b.bar_index:>4})"
            )

    lines.extend([
        "",
        "4. DETECTED 63-BAR LEGS (AC - COMPOSITE CYCLE LEG)",
        "-" * 100,
    ])
    if not analysis["63_matches"]:
        lines.append("No 63-bar composite legs matched.")
    else:
        for idx, item in enumerate(analysis["63_matches"], 1):
            a, b = item["a"], item["b"]
            lines.append(
                f"{idx:02d}. {a.datetime_obj:%d-%b %H:%M} (Bar {a.bar_index:>4}) "
                f"--({item['bars']:>2} bars | err:{item['error']:+d})--> "
                f"{b.datetime_obj:%d-%b %H:%M} (Bar {b.bar_index:>4})"
            )

    # Save report
    report_content = "\n".join(lines) + "\n"
    REPORT_FILE.write_text(report_content, encoding="utf-8")

    # Console display
    print("\n" + report_content)
    print(f"Report saved to: {REPORT_FILE}")


if __name__ == "__main__":
    run_analysis()