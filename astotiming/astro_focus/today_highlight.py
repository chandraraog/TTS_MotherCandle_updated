"""Display-only current-day highlighting, using Mumbai calendar dates."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from nse_calendar import sessions, overlaps

def is_today(row, day, market_only=False):
    try:
        if market_only:
            return day in sessions(day.year, day.month) and overlaps(row, day)
        start = datetime.fromisoformat(row['start_ist'])
        end = row.get('end_ist') or ''
        if not end:
            return start.date() == day
        finish = datetime.fromisoformat(end)
        if len(end) == 10:
            finish += timedelta(days=1)
        midnight = datetime.combine(day, datetime.min.time())
        return start < midnight + timedelta(days=1) and finish > midnight
    except (ValueError, TypeError, KeyError):
        return False

def styled_events(items, day=None, market_only=False):
    import pandas as pd
    day = day or datetime.now(ZoneInfo('Asia/Kolkata')).date()
    df = pd.DataFrame(items)
    if df.empty:
        return df
    return df.style.apply(lambda row: ['background-color: #fff1b8; color: #342900; font-weight: bold' if is_today(row, day, market_only) else '' for _ in row], axis=1)
