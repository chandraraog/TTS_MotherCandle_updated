from datetime import datetime

from config import settings
from utils import (
    find_index,
    epoch_to_datetime,
    project_market_time,
)


def calculate_cycles(candles):
    """
    Calculate the current running cycle,
    next upcoming cycle and bar gap.
    """

    origin1_idx = find_index(candles, settings.cycle.origin_1)
    origin2_idx = find_index(candles, settings.cycle.origin_2)

    if origin1_idx is None or origin2_idx is None:
        raise Exception("Origin points not found.")

    bar_gap = origin2_idx - origin1_idx

    current_time = datetime.now()

    cycle_index = origin2_idx
    current_running = None

    while cycle_index < len(candles):

        candle_time = epoch_to_datetime(candles[cycle_index][0])

        if candle_time <= current_time:
            current_running = cycle_index

        cycle_index += bar_gap

    next_upcoming = None

    if current_running is not None:
        next_upcoming = current_running + bar_gap

    return current_running, next_upcoming, bar_gap


def get_current_cycle(candles, current_running):
    """
    Return current cycle information.
    """

    if current_running is None:
        return None

    candle = candles[current_running]

    return {
        "datetime": epoch_to_datetime(candle[0]),
        "open": candle[1],
        "high": candle[2],
        "low": candle[3],
        "close": candle[4],
        "volume": candle[5]
    }


def get_next_cycle_time(candles, current_running, next_upcoming):
    """
    Return the next cycle datetime.
    """

    if next_upcoming is None:
        return None

    if next_upcoming < len(candles):
        return epoch_to_datetime(candles[next_upcoming][0])

    bars_left = next_upcoming - current_running

    current_time = epoch_to_datetime(
        candles[current_running][0]
    )

    return project_market_time(current_time, bars_left)


def detect_time_price_square(
    candles,
    current_running,
    bar_gap,
    tolerance=0.0,
    lookback_cycles=3
):
    """
    Compare current cycle with previous cycle candles.

    Match Conditions:
    1. Current High == Previous High
    2. Current Low == Previous Low
    3. Previous High lies inside Current High-Low range
    4. Previous Low lies inside Current High-Low range

    Returns:
    {
        "is_tps": True/False,
        "strength": 0-3,
        "matches": []
    }
    """

    if current_running is None:
        return {
            "is_tps": False,
            "strength": 0,
            "matches": []
        }

    current = candles[current_running]

    current_high = current[2]
    current_low = current[3]

    matches = []

    for i in range(1, lookback_cycles + 1):

        prev_index = current_running - (bar_gap * i)

        if prev_index < 0:
            break

        previous = candles[prev_index]

        previous_high = previous[2]
        previous_low = previous[3]

        high_match = abs(current_high - previous_high) <= tolerance
        low_match = abs(current_low - previous_low) <= tolerance

        high_touch = current_low <= previous_high <= current_high
        low_touch = current_low <= previous_low <= current_high

        if high_match or low_match or high_touch or low_touch:

            matches.append({
                "cycle": i,
                "datetime": epoch_to_datetime(previous[0]),
                "high": previous_high,
                "low": previous_low,
                "reason": {
                    "high_match": high_match,
                    "low_match": low_match,
                    "high_touch": high_touch,
                    "low_touch": low_touch
                }
            })

    return {
        "is_tps": len(matches) > 0,
        "strength": len(matches),
        "matches": matches
    }
