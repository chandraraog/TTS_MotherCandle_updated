from datetime import datetime, timedelta, date, time

import numpy as np

# ============================================================
# Canonical session bar-index
#
# Converts any intraday datetime into ONE global integer bar index,
# counted in session bars of a given resolution since a fixed Monday
# epoch (skipping weekends). This lets cycle math (AB/BC/AC lengths,
# and projecting the nearest cycle-line date to "now") be computed
# purely from calendar arithmetic on the ORIGINAL A/B/C anchor dates
# from config.json - without needing those candles to still be
# present in whatever limited window of history got fetched.
# ============================================================

SESSION_START_HOUR = 9
SESSION_START_MIN = 15
SESSION_MINUTES = 375  # NSE cash session: 09:15 - 15:30
CANONICAL_EPOCH_DATE = date(2000, 1, 3)  # a Monday, arbitrary fixed reference


def candles_per_day(resolution_minutes: int) -> int:
    """
    Number of session bars per trading day for this resolution.
    Uses ceiling division so a resolution that doesn't divide the
    375-minute session evenly (e.g. 30m -> 375/30 = 12.5) still
    accounts for the trailing partial candle (12:45-... final 15min
    bar) as its own bar, instead of silently dropping it - which
    would otherwise throw off the weekday-only fallback estimate
    used when AB_bars/BC_bars/AC_bars aren't supplied in config.json.
    """
    return -(-SESSION_MINUTES // resolution_minutes)  # ceiling division


def canonical_bar_index(dt: datetime, resolution_minutes: int) -> int:
    """
    Global bar-index for `dt`, independent of any fetched dataframe.
    """
    d = dt.date()
    trading_days = int(np.busday_count(CANONICAL_EPOCH_DATE, d))
    session_open = dt.replace(hour=SESSION_START_HOUR, minute=SESSION_START_MIN, second=0, microsecond=0)
    slot = int((dt - session_open).total_seconds() // (resolution_minutes * 60))
    return trading_days * candles_per_day(resolution_minutes) + slot


def bar_index_to_datetime(index: int, resolution_minutes: int) -> datetime:
    """
    Inverse of canonical_bar_index: bar-index -> the intraday datetime
    it represents (skips weekends).
    """
    per_day = candles_per_day(resolution_minutes)
    trading_days, slot = divmod(index, per_day)
    target = np.busday_offset(CANONICAL_EPOCH_DATE, trading_days, roll="forward")
    target_date = target.astype("datetime64[D]").astype(date)
    return datetime.combine(target_date, time(SESSION_START_HOUR, SESSION_START_MIN)) + timedelta(minutes=resolution_minutes * slot)


def epoch_to_datetime(epoch):
    """
    Convert Unix timestamp to datetime.
    """
    return datetime.fromtimestamp(epoch)


def datetime_to_string(dt):
    """
    Format datetime as YYYY-MM-DD HH:MM
    """
    return dt.strftime("%Y-%m-%d %H:%M")


def find_index(candles, target_datetime):
    """
    Find candle index for the given datetime string.
    """

    for index, candle in enumerate(candles):

        candle_time = epoch_to_datetime(candle[0])

        if datetime_to_string(candle_time) == target_datetime:
            return index

    return None


def next_trading_day(dt):
    """
    Move to next trading day (skip weekends).
    """

    dt = dt + timedelta(days=1)

    while dt.weekday() >= 5:
        dt = dt + timedelta(days=1)

    return dt.replace(hour=9, minute=15, second=0, microsecond=0)


def project_market_time(start_time, bars):
    """
    Project future candle time considering market hours.
    """

    current = start_time

    while bars > 0:

        current += timedelta(minutes=3)

        # Skip weekends
        if current.weekday() >= 5:
            current = next_trading_day(current)
            continue

        market_open = current.replace(
            hour=9,
            minute=15,
            second=0,
            microsecond=0
        )

        market_close = current.replace(
            hour=15,
            minute=27,
            second=0,
            microsecond=0
        )

        if current < market_open:
            current = market_open

        elif current > market_close:
            current = next_trading_day(current)

        bars -= 1

    return current
