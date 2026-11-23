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


def parse_month(html, year, month, require_ist=True):
    import calendar
    soup = BeautifulSoup(html, 'html.parser')
    expected = date(year, month, 1).strftime('%B %Y')
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    if expected not in title:
        raise ValueError(f'Website did not return {expected}: {title}')
    # The actual Mumbai response identifies IST in the monthly introduction.
    # Require the location as well: IST alone is ambiguous internationally.
    notes = [p.get_text(' ', strip=True) for p in soup.find_all('p')]
    intro = [n for n in notes if 'planet aspects at' in n.lower()]
    form = soup.select_one('form#frmAstro')
    loc = form.select_one('input[name=loc]') if form else None
    location = form.select_one('input[name=location]') if form else None
    confirmed = any(re.search(
        r'planet aspects at\s+Mumbai,\s*Maharashtra,\s*India\s*\(IST\)',
        n, re.I) and expected in n for n in intro)
    if require_ist and not (confirmed and loc and loc.get('value') == '1275339'
                            and location and location.get('value', '').strip() == 'Mumbai, Maharashtra, India'):
        raise ValueError('Source timezone/location not confirmed as Mumbai IST (UTC+05:30): ' + ' '.join(intro))
    days = set()
    events = []
    for tab in soup.select('.accordion-tab'):
        label = tab.select_one('label.accordion-tab-label')
        table = tab.select_one('table.aspect-table')
        if not label or not table:
            continue
        current = datetime.strptime(label.get_text(' ', strip=True), '%B %d, %Y').date()
        if (current.year, current.month) != (year, month):
            raise ValueError('Unexpected date in aspect table: ' + str(current))
        days.add(current.day)
        for row in table.select('tbody tr'):
            cells = row.find_all('td')
            if len(cells) < 2:
                continue
            clock = cells[0].get_text(' ', strip=True)
            if not TIME_RE.fullmatch(clock):
                raise ValueError('Unrecognized aspect time: ' + clock)
            pair = PAIR_RE.match(cells[1].get_text(' ', strip=True))
            if not pair:
                continue
            p1, aspect, p2 = pair.groups()
            aspect = aspect.title()
            degree, bias = ASPECTS[aspect]
            events.append(dict(date=current.isoformat(), time=clock, planet1=p1, planet2=p2, aspect=aspect, degree=degree, bias=bias, source=URL))
    if days != set(range(1, calendar.monthrange(year, month)[1]+1)):
        raise ValueError(f'Incomplete monthly aspect tables: {len(days)} days found for {expected}.')
    return events


def fetch_month(year, month):
    """Submit the observed POST form; cache only validated Mumbai/IST HTML."""
    target = date(year, month, 1)
    root = Path(__file__).resolve().parent
    debug = root / 'output' / 'timing_debug'
    cache = root / 'data' / 'timing_cache' / f'{year}-{month:02}-IST.html'
    debug.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        try:
            return parse_month(cache.read_text(encoding='utf-8'), year, month)
        except ValueError:
            pass
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.9', 'Referer': URL}
    # Mumbai GeoNames ID; the returned timezone must independently confirm IST.
    payload = {'year': str(year), 'month': str(month), 'location': 'Mumbai, Maharashtra, India',
               'loc': '1275339', 'p': '1', 'mode': 'monthly', 'is_amp': ''}
    errors = []
    with requests.Session() as session:
        session.headers.update(headers)
        try:
            initial = session.get(URL, timeout=25)
            initial.raise_for_status()
            form = BeautifulSoup(initial.text, 'html.parser').select_one('form#frmAstro')
            if not form or form.get('method','').upper() != 'POST':
                raise ValueError('Expected Prokerala POST form not found.')
        except (requests.RequestException, ValueError) as exc:
            errors.append(str(exc))
        from urllib.parse import urljoin
        monthly_url = urljoin(URL, f'planet-aspects-{target:%B}'.lower() + f'-{year}.html')
        for i, endpoint in enumerate((URL, monthly_url)):
            try:
                response = session.post(endpoint, data=payload, timeout=30)
                response.raise_for_status()
                (debug / f'{year}-{month:02}_post_{i}.html').write_text(response.text, encoding='utf-8')
                rows = parse_month(response.text, year, month)
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(response.text, encoding='utf-8')
                return rows
            except (requests.RequestException, ValueError) as exc:
                errors.append(str(exc))
    raise ValueError(f'Prokerala POST did not return a validated {target:%B %Y} IST page. '
                     f'Last error: {errors[-1]}. Debug HTML: {debug}. No wrong-month or wrong-timezone timings were used.')


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