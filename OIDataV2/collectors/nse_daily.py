"""Independent NSE daily collector; never imports or writes OIData references."""
import io
import json
import zipfile
from datetime import timedelta

import pandas as pd
import requests

from calculations import number, difference, interpretation, net_view
from collectors.fyers_data import stamp


def analyse(frame, day):
    required = {'FinInstrmTp', 'TckrSymb', 'XpryDt', 'ClsPric', 'PrvsClsgPric',
                'OpnIntrst', 'ChngInOpnIntrst', 'StrkPric', 'OptnTp'}
    if not required.issubset(frame.columns):
        raise RuntimeError('NSE bhavcopy schema changed; required fields are missing.')
    frame = frame.copy()
    frame['XpryDt'] = pd.to_datetime(frame['XpryDt'], errors='coerce')
    for col in ('ClsPric', 'PrvsClsgPric', 'OpnIntrst', 'ChngInOpnIntrst', 'StrkPric'):
        frame[col] = pd.to_numeric(frame[col], errors='coerce')
    frame = frame[frame['XpryDt'] >= pd.Timestamp(day)]
    futures = frame[frame['FinInstrmTp'].isin(['STF', 'IDF'])].sort_values(['TckrSymb', 'XpryDt'])
    opts = frame[frame['FinInstrmTp'].isin(['STO', 'IDO'])]
    rows = []
    for symbol, future_group in futures.groupby('TckrSymb', sort=True):
        future_group = future_group.drop_duplicates('XpryDt').sort_values('XpryDt')
        options = opts[opts['TckrSymb'] == symbol]
        option_dates = sorted(options['XpryDt'].dropna().unique())
        option_groups = [options[options['XpryDt'] == expiry] for expiry in option_dates[:2]]
        future_rows = [item for _, item in future_group.head(2).iterrows()]
        row = {'Date': day.isoformat(), 'Symbol': symbol}
        monthly_future = future_rows[0] if future_rows else None
        monthly_expiry = monthly_future['XpryDt'] if monthly_future is not None else None
        monthly_options = options[options['XpryDt'] == monthly_expiry] if monthly_expiry is not None else options.iloc[0:0]
        row['Monthly Future Expiry'] = monthly_expiry.date().isoformat() if monthly_expiry is not None else None
        row['Monthly Fut OI Chg'] = number(monthly_future['ChngInOpnIntrst']) if monthly_future is not None else None
        row['Monthly Option Expiry'] = monthly_expiry.date().isoformat() if not monthly_options.empty else None
        for side in ('CE', 'PE'):
            monthly_side = monthly_options[monthly_options['OptnTp'] == side]
            row[f'Monthly Total {side} OI Chg'] = number(monthly_side['ChngInOpnIntrst'].sum(min_count=1))
        next_monthly_future = future_rows[1] if len(future_rows) > 1 else None
        next_monthly_expiry = next_monthly_future['XpryDt'] if next_monthly_future is not None else None
        next_monthly_options = options[options['XpryDt'] == next_monthly_expiry] if next_monthly_expiry is not None else options.iloc[0:0]
        row['Next Monthly Future Expiry'] = next_monthly_expiry.date().isoformat() if next_monthly_expiry is not None else None
        row['Next Monthly Fut OI Chg'] = number(next_monthly_future['ChngInOpnIntrst']) if next_monthly_future is not None else None
        row['Next Monthly Option Expiry'] = next_monthly_expiry.date().isoformat() if not next_monthly_options.empty else None
        for side in ('CE', 'PE'):
            next_monthly_side = next_monthly_options[next_monthly_options['OptnTp'] == side]
            row[f'Next Monthly Total {side} OI Chg'] = number(next_monthly_side['ChngInOpnIntrst'].sum(min_count=1))
        for rank in range(2):
            prefix = '' if rank == 0 else 'Next '
            if rank < len(future_rows):
                future = future_rows[rank]
                price = number(future['ClsPric'])
                term, view = interpretation('FUT', difference(price, future['PrvsClsgPric']), future['ChngInOpnIntrst'])
                expiry = future['XpryDt'].date().isoformat()
                row.update({f'{prefix}Future Expiry': expiry, f'{prefix}Fut Close': price,
                            f'{prefix}Fut OI': number(future['OpnIntrst']),
                            f'{prefix}Fut OI Chg': number(future['ChngInOpnIntrst']),
                            f'{prefix}Future': term, f'{prefix}Fut View': view})
            else:
                price, view = None, '—'
                row.update({f'{prefix}Future Expiry': None, f'{prefix}Fut Close': None,
                            f'{prefix}Fut OI': None, f'{prefix}Fut OI Chg': None,
                            f'{prefix}Future': '—', f'{prefix}Fut View': '—'})

            expiry_options = option_groups[rank] if rank < len(option_groups) else options.iloc[0:0]
            views = [view]
            if not expiry_options.empty and price is not None:
                atm = expiry_options.loc[(expiry_options['StrkPric'] - price).abs().idxmin(), 'StrkPric']
                row[f'{prefix}ATM'] = number(atm)
                row[f'{prefix}Option Expiry'] = expiry_options['XpryDt'].iloc[0].date().isoformat()
                for side in ('CE', 'PE'):
                    all_side = expiry_options[expiry_options['OptnTp'] == side]
                    row[f'{prefix}Total {side} OI'] = number(all_side['OpnIntrst'].sum(min_count=1))
                    row[f'{prefix}Total {side} OI Chg'] = number(all_side['ChngInOpnIntrst'].sum(min_count=1))
                    match = all_side[all_side['StrkPric'] == atm]
                    item = match.iloc[0] if not match.empty else {}
                    term, side_view = interpretation(side, difference(item.get('ClsPric'), item.get('PrvsClsgPric')), item.get('ChngInOpnIntrst'))
                    row.update({f'{prefix}{side} OI': number(item.get('OpnIntrst')),
                                f'{prefix}{side} OI Chg': number(item.get('ChngInOpnIntrst')),
                                f'{prefix}{side} Term': term, f'{prefix}{side} View': side_view})
                    views.append(side_view)
                ce, pe = row.get(f'{prefix}Total CE OI'), row.get(f'{prefix}Total PE OI')
                row[f'{prefix}PCR'] = pe / ce if ce and pe is not None else None
            else:
                row[f'{prefix}ATM'] = None
                row[f'{prefix}Option Expiry'] = None
                for side in ('CE', 'PE'):
                    row.update({f'{prefix}{side} OI': None, f'{prefix}{side} OI Chg': None,
                                f'{prefix}{side} Term': '—', f'{prefix}{side} View': '—',
                                f'{prefix}Total {side} OI': None, f'{prefix}Total {side} OI Chg': None})
                row[f'{prefix}PCR'] = None
            row['Net View' if rank == 0 else 'Next Net View'] = net_view(views)
        rows.append(row)
    if not rows:
        raise RuntimeError('NSE bhavcopy contains no usable futures rows.')
    return rows


class NSE:
    def __init__(self, root):
        self.cache = root / 'data' / 'nse'
        self.cache.mkdir(parents=True, exist_ok=True)

    def collect(self, cfg, today, progress=lambda message: None):
        collected, missing = [], []
        session = requests.Session()
        session.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.nseindia.com/'})
        for offset in range(35):
            day = today - timedelta(days=offset)
            if day.weekday() >= 5:
                continue
            progress(f'Looking for NSE daily data: {day}')
            path = self.cache / f'{day}.json'
            payload = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
            if payload is not None and payload.get('format_version') != 4:
                payload = None
            if payload is None:
                url = f'https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv.zip'
                try:
                    response = session.get(url, timeout=(10, 30))
                    if response.status_code == 404:
                        missing.append(day.isoformat())
                        continue
                    response.raise_for_status()
                    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                        name = next(n for n in archive.namelist() if n.lower().endswith('.csv'))
                        frame = pd.read_csv(archive.open(name))
                    payload = {'format_version': 4, 'source': 'NSE bhavcopy', 'trade_date': day.isoformat(), 'collected_at': stamp(),
                               'rows': analyse(frame, day), 'errors': [], 'comparison': 'OI change versus previous trading day. Current/next futures and current/next options expiries are ranked separately; NIFTY options may have weekly expiries.'}
                    path.write_text(json.dumps(payload, allow_nan=False), encoding='utf-8')
                except (requests.RequestException, zipfile.BadZipFile, StopIteration):
                    raise RuntimeError(f'NSE download failed for {day}; previous saved data remains available.') from None
            # Preserve the full daily cache for other tools and future thresholds.
            minimum = cfg.get('min_price', 0)
            payload = dict(payload)
            payload['rows'] = [row for row in payload['rows'] if minimum == 0 or (number(row.get('Fut Close')) is not None and number(row.get('Fut Close')) > minimum)]
            payload['min_price'] = minimum
            collected.append(payload)
            if len(collected) >= cfg['history_days']:
                break
        if not collected:
            raise RuntimeError('No NSE bhavcopies available in the last 35 calendar days.')
        return collected, missing
