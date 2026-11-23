"""
Time Cycle Research Engine
Author : Chandra Rao

Goal:
Find

Point A ----18 bars---- Point B ----18 bars---- Point C

where

Bars(A->B) == Bars(B->C)
"""

import pandas as pd
from datetime import datetime

from fyers_client import fetch_history

# ==========================================================
# SETTINGS
# ==========================================================

PIVOT_STRENGTH = 5      # candles left/right
SHOW_ALL_PIVOTS = False

# ==========================================================
# LOAD DATA
# ==========================================================


def load_data():

    candles = fetch_history()

    df = pd.DataFrame(
        candles,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    df["datetime"] = (
        pd.to_datetime(df["timestamp"], unit="s", utc=True)
        .dt.tz_convert("Asia/Kolkata")
        .dt.tz_localize(None)
    )

    return df


# ==========================================================
# FIND PIVOTS
# ==========================================================

def detect_pivots(df, strength):

    pivots = []

    highs = df["high"].values
    lows = df["low"].values

    total = len(df)

    for i in range(strength, total-strength):

        left_high = highs[i-strength:i]
        right_high = highs[i+1:i+strength+1]

        left_low = lows[i-strength:i]
        right_low = lows[i+1:i+strength+1]

        current_high = highs[i]
        current_low = lows[i]

        # --------------------------
        # Pivot High
        # --------------------------

        if (
            current_high > max(left_high)
            and
            current_high > max(right_high)
        ):

            pivots.append({

                "index": i,

                "time": df.iloc[i]["datetime"],

                "price": current_high,

                "type": "HIGH"
            })

            continue

        # --------------------------
        # Pivot Low
        # --------------------------

        if (
            current_low < min(left_low)
            and
            current_low < min(right_low)
        ):

            pivots.append({

                "index": i,

                "time": df.iloc[i]["datetime"],

                "price": current_low,

                "type": "LOW"
            })

    return pivots


# ==========================================================
# REMOVE INVALID PIVOTS
# ==========================================================

def clean_pivots(pivots):

    if not pivots:
        return []

    cleaned = [pivots[0]]

    for p in pivots[1:]:

        last = cleaned[-1]

        # --------------------------------------------------
        # Same pivot type
        # Keep only the stronger pivot
        # --------------------------------------------------

        if p["type"] == last["type"]:

            if p["type"] == "HIGH":

                if p["price"] > last["price"]:
                    cleaned[-1] = p

            else:

                if p["price"] < last["price"]:
                    cleaned[-1] = p

        else:

            cleaned.append(p)

    return cleaned


# ==========================================================
# PRINT PIVOTS (OPTIONAL)
# ==========================================================

def print_pivots(pivots):

    print("\n")
    print("=" * 80)
    print("PIVOTS")
    print("=" * 80)

    for i, p in enumerate(pivots):

        print(
            f"{i+1:03d} | "
            f"{p['type']:<5} | "
            f"{p['time']} | "
            f"{p['price']:.2f}"
        )


# ==========================================================
# FIND MATCHING TIME CYCLES
# ==========================================================

def find_matching_cycles(pivots):

    print("\n")
    print("=" * 80)
    print("MATCHING TIME CYCLES")
    print("=" * 80)

    matches = 0

    for i in range(len(pivots) - 2):

        A = pivots[i]
        B = pivots[i + 1]
        C = pivots[i + 2]

        bars_ab = B["index"] - A["index"]
        bars_bc = C["index"] - B["index"]

        if bars_ab != bars_bc:
            continue

        matches += 1

        print()
        print("=" * 80)
        print(f"MATCH #{matches}")
        print("=" * 80)

        print(f"Cycle Length : {bars_ab} Bars")

        print()

        print(
            f"A : {A['type']:<4} | "
            f"{A['time']} | "
            f"{A['price']:.2f}"
        )

        print(" " * 10 + f"|--- {bars_ab} Bars --->")

        print(
            f"B : {B['type']:<4} | "
            f"{B['time']} | "
            f"{B['price']:.2f}"
        )

        print(" " * 10 + f"|--- {bars_bc} Bars --->")

        print(
            f"C : {C['type']:<4} | "
            f"{C['time']} | "
            f"{C['price']:.2f}"
        )

    if matches == 0:

        print("\nNo matching cycles found.")

# ==========================================================
# MAIN
# ==========================================================


def main():

    print("=" * 80)
    print("TIME CYCLE RESEARCH ENGINE")
    print("=" * 80)

    print("\nLoading historical data...")

    df = load_data()

    print(f"Total Candles : {len(df)}")

    print("\nDetecting pivots...")

    pivots = detect_pivots(df, PIVOT_STRENGTH)

    print(f"Raw Pivots : {len(pivots)}")

    pivots = clean_pivots(pivots)

    print(f"Clean Pivots : {len(pivots)}")

    if SHOW_ALL_PIVOTS:
        print_pivots(pivots)

    find_matching_cycles(pivots)


# ==========================================================
# START
# ==========================================================

if __name__ == "__main__":
    main()
