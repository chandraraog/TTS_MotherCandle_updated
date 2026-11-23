import argparse
import csv
import json
import re
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = 'https://www.prokerala.com/astrology/planetary-aspects.html'
ASPECTS = {'Conjunction': (0, 'Context dependent'), 'Semi Sextile': (30, 'Dwidwadasha'), 'Square': (90, 'Bearish'), 'Trine': (120, 'Favourable - Bhrigu Nandi Nadi'), 'Quincunx': (150, 'Shadashtaka'), 'Opposition': (180, 'Tensed / Adverse')}
FIELDS = ['date', 'time', 'planet1', 'planet2', 'aspect', 'degree', 'bias', 'source']
DATE_RE = re.compile(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b')
TIME_RE = re.compile(r'\b\d{1,2}:\d{2}\s*[ap]m\b', re.I)
PAIR_RE = re.compile(r'^([A-Za-z]+)\s+(' + '|'.join(ASPECTS) + r')\s+([A-Za-z]+)\b', re.I)


def parse_month(html, year, month):
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(' ', strip=True)
    expected = date(year, month, 1).strftime('%B %Y')
    if expected not in text and expected not in (soup.title.get_text() if soup.title else ''):
        raise ValueError(f'Website did not return {expected}. Check date parameters.')
    # Insert date labels from form controls into the visible text stream.
    for el in soup.find_all('input'):
        for attr in ('value', 'data-date', 'aria-label', 'placeholder'):
            value = el.get(attr, '')
            if DATE_RE.search(value):
                el.replace_with(' ' + value + ' ')
                break
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                el.replace_with(' ' + date.fromisoformat(value).strftime('%B %d, %Y') + ' ')
                break
    text = soup.get_text(' ', strip=True)
    # Match dates and time/aspect pairs in document order, independent of tags.
    token = re.compile(DATE_RE.pattern + r'|(?P<clock>\d{1,2}:\d{2}\s*[ap]m)\s+(?P<pair>[A-Za-z]+\s+(?:[A-Za-z]+\s+){1,3}[A-Za-z]+)', re.I)
    current = None
    seen_dates = set()
    rows = 0
    events = []
    for match in token.finditer(text):
        if not match.group('clock'):
            current = datetime.strptime(match.group(), '%B %d, %Y').date()
            continue
        if current is None or (current.year, current.month) != (year, month):
            continue
        seen_dates.add(current)
        rows += 1
        pair = PAIR_RE.match(match.group('pair'))
        if not pair:
            continue
        p1, aspect, p2 = pair.groups()
        aspect = aspect.title()
        degree, bias = ASPECTS[aspect]
        events.append(dict(date=current.isoformat(), time=match.group('clock'), planet1=p1, planet2=p2, aspect=aspect, degree=degree, bias=bias, source=URL))
    if not rows or not seen_dates:
        raise ValueError('No dated aspect rows parsed. Inspect saved debug HTML.')
    return events


def fetch_month(year, month):
    response = requests.get(URL, params={'year': year, 'month': month}, headers={'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.9'}, timeout=30)
    response.raise_for_status()
    try:
        return parse_month(response.text, year, month)
    except ValueError:
        debug = Path(__file__).resolve().parent / f"prokerala_debug_{year}_{month:02}.html"
        debug.write_text(response.text, encoding="utf-8")
        print(f"Debug HTML saved: {debug}")
        raise


def get_timing_candles(target):
    if isinstance(target, str):
        target = date.fromisoformat(target)
    return [event for event in fetch_month(target.year, target.month)
            if event['date'] == target.isoformat()]


def format_time(value, mode="24h"):
    clock = datetime.strptime(value.strip().upper(), "%I:%M %p")
    return clock.strftime("%H:%M") if mode == "24h" else clock.strftime("%I:%M %p").lower()


def main():
    parser = argparse.ArgumentParser(description='Prokerala timing candles: website date and time only')
    parser.add_argument('date', nargs='?', default=date.today().isoformat())
    parser.add_argument("--format", choices=["24h", "12h"], default="24h")
    args = parser.parse_args()
    try:
        target = date.fromisoformat(args.date)
        events = get_timing_candles(target)
        print(f'\nTiming Candles | {target}')
        for e in events:
            e['time'] = format_time(e['time'], args.format)
        for e in events:
            print(f"{e['date']} {e['time']} , {e['planet1']} - {e['planet2']}, {e['aspect']} ({e['degree']} deg) | {e['date']} {e['time']} | {e['bias']}")
        print(f'Total matches: {len(events)}')
        out = Path(__file__).resolve().parent / 'output'
        out.mkdir(exist_ok=True)
        base = out / f'timing_candles_{target}'
        with base.with_suffix('.csv').open('w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(events)
        base.with_suffix('.json').write_text(json.dumps(events, indent=2), encoding='utf-8')
        print(f'Files saved in {out}')
    except (requests.RequestException, ValueError, OSError) as exc:
        parser.exit(1, f'ERROR: {exc}\n')


if __name__ == '__main__':
    main()