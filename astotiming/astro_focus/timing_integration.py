"""Cache Prokerala timings, normalizing verified source timezone to IST."""
import csv, re, calendar
from datetime import datetime, date
from zoneinfo import ZoneInfo
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from timing_candles import URL, FIELDS, parse_month, format_time
ROOT=Path(__file__).parent
IST=ZoneInfo('Asia/Kolkata')
def cache_path(y,m): return ROOT/'data'/f'{y}-{m:02}'/'timing_candles.csv'
def read_timings(y,m):
    p=cache_path(y,m)
    if not p.exists(): return None
    with p.open(encoding='utf-8-sig',newline='') as f: return list(csv.DictReader(f))
def line(e):
    stamp=f"{e['date']} {e['time']}"
    return f"{stamp} , {e['planet1']} - {e['planet2']}, {e['aspect']} ({e['degree']} deg) | {stamp} | {e['bias']}"
def fetch_day(target):
    """Use the original working provider; preserve website date/time, no adjacent months."""
    from timing_candles import get_timing_candles
    rows=[dict(e,time=format_time(e['time'])) for e in get_timing_candles(target)]
    return sorted(rows,key=lambda e:e['time'])
