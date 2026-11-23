import copy
import json
import math
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from calculations import bias, difference
from collectors.fyers_data import Fyers, money_scope, stamp
from collectors.nse_daily import NSE
from storage import Store
from money_alerts import MoneyAlerts

IST = timezone(timedelta(hours=5, minutes=30))
JOBS = ('money', 'oi', 'nse')


def now_ist():
    return datetime.now(IST)


def validate(cfg):
    if isinstance(cfg, dict) and isinstance(cfg.get('oi'), dict):
        cfg['oi'].setdefault('min_price', 1000)
    expected = {'money', 'oi', 'nse', 'market', 'requests_per_second', 'requests_per_minute', 'requests_per_day', 'retention_days'}
    if not isinstance(cfg, dict) or set(cfg) != expected:
        raise ValueError('Configuration sections must match config.json.')
    fields = {'money': {'symbol', 'expiry', 'strikes', 'interval_seconds', 'oi_multiplier'},
              'oi': {'universe', 'symbols', 'interval_seconds', 'mode', 'daily_time', 'min_price'},
              'nse': {'enabled', 'daily_time', 'retry_seconds', 'history_days'},
              'market': {'start', 'end', 'holidays'}}
    for section, keys in fields.items():
        if not isinstance(cfg[section], dict) or set(cfg[section]) != keys:
            raise ValueError(f'Invalid fields in {section}.')
    def numeric(value, low, high, label, integer=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high or (integer and int(value) != value):
            raise ValueError(f'{label} must be between {low} and {high}.')
    def symbol(value, exchanges='NSE'):
        if not isinstance(value, str) or not re.fullmatch(r'(?:'+exchanges+r'):[A-Z0-9&_.-]{1,60}', value):
            raise ValueError('Use a valid FYERS exchange:symbol identifier.')
    m, o, n, market = (cfg[k] for k in ('money', 'oi', 'nse', 'market'))
    symbol(m['symbol'], 'NSE|BSE|MCX')
    if not isinstance(m['expiry'], str) or not re.fullmatch(r'\d{0,12}', m['expiry']):
        raise ValueError('Expiry must be empty (nearest) or an expiry timestamp.')
    numeric(m['strikes'], 1, 50, 'Strikes', True)
    numeric(m['interval_seconds'], 0, 86400, 'Money interval', True)
    if 0 < m['interval_seconds'] < 30:
        raise ValueError('Money interval must be 0 (Off) or at least 30 seconds.')
    numeric(m['oi_multiplier'], 0.000001, 100000, 'OI multiplier')
    numeric(o['min_price'], 0, 10000000, 'OI minimum price')
    numeric(o['interval_seconds'], 300, 86400, 'OI interval', True)
    if o['mode'] not in ('hourly', 'daily') or o['universe'] not in ('all', 'custom'):
        raise ValueError('Invalid OI mode/universe.')
    if not isinstance(o['symbols'], list) or not 1 <= len(o['symbols']) <= 1000:
        raise ValueError('Provide 1–1000 custom symbols.')
    for s in o['symbols']:
        symbol(s)
    if not isinstance(n['enabled'], bool):
        raise ValueError('NSE enabled must be true or false.')
    numeric(n['retry_seconds'], 300, 86400, 'NSE retry interval', True)
    numeric(n['history_days'], 1, 20, 'History days', True)
    for value in (o['daily_time'], n['daily_time'], market['start'], market['end']):
        if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
            raise ValueError('Times must use HH:MM in IST.')
    if market['start'] >= market['end']:
        raise ValueError('Market end must be later than start.')
    if not isinstance(market['holidays'], list):
        raise ValueError('Holidays must be a list of YYYY-MM-DD dates.')
    for value in market['holidays']:
        datetime.strptime(value, '%Y-%m-%d')
    numeric(cfg['requests_per_second'], 0.1, 5, 'Request rate')
    numeric(cfg['requests_per_minute'], 1, 500, 'Requests per minute', True)
    numeric(cfg['requests_per_day'], 1, 500000, 'Requests per day', True)
    numeric(cfg['retention_days'], 7, 3650, 'Retention days', True)
    return cfg


def in_session(now, cfg):
    return trading_day(now, cfg) and cfg['market']['start'] <= now.strftime('%H:%M') <= cfg['market']['end']


def trading_day(now, cfg):
    return now.weekday() < 5 and now.date().isoformat() not in cfg['market']['holidays']


def add_changes(payload, previous, job):
    """Compare identical contracts only, within the same trading date."""
    old = {}
    if previous and datetime.fromisoformat(previous['collected_at']).astimezone(IST).date() == datetime.fromisoformat(payload['collected_at']).astimezone(IST).date():
        old = {r.get('symbol') if job == 'money' else r.get('Future Contract'): r for r in previous['rows']}
    for row in payload['rows']:
        key = row.get('symbol') if job == 'money' else row.get('Future Contract')
        last = old.get(key, {})
        if job == 'money':
            row['snapshot_oi_change'] = difference(row.get('oi'), last.get('oi'))
            row['snapshot_money_change'] = difference(row.get('money'), last.get('money'))
        else:
            row['Fut OI Since Snapshot'] = difference(row.get('Fut OI'), last.get('Fut OI'))
    payload['previous_snapshot_at'] = previous['collected_at'] if old else None


def demo_payload(job, cfg):
    tick = int(time.time() / 10) % 9
    if job == 'money':
        rows = []
        for strike in range(25000 - cfg['strikes'] * 50, 25000 + cfg['strikes'] * 50 + 1, 50):
            for side in ('CE', 'PE'):
                delta = (10000 + (strike % 300) * 100 + tick * 500) * (1.4 if side == 'PE' else 1)
                if strike == 25050:
                    delta = -delta
                atp = 100 + abs(strike - 25000) / 10
                rows.append({'symbol': f'DEMO:{strike}{side}', 'strike': strike, 'type': side, 'expiry': 'DEMO',
                             'oi': 100000 + delta, 'previous_oi': 100000, 'oi_change': delta, 'atp': atp,
                             'ltp': atp + 3, 'money': delta * atp * cfg['oi_multiplier'] / 100000,
                             'observed_at': stamp(), 'last_trade_time': int(time.time())})
        totals = {s: sum(r['money'] for r in rows if r['type'] == s) for s in ('CE', 'PE')}
        return {'source': 'DEMO — synthetic', 'started_at': stamp(), 'collected_at': stamp(), 'rows': rows,
                'errors': [], 'symbol': cfg['symbol'], 'expiry': {'date': 'DEMO', 'expiry': '9999999999'},
                'expiries': [{'date': 'DEMO', 'expiry': '9999999999'}], 'spot': 25012.5,
                'totals': totals, 'bias': bias(totals['CE'], totals['PE']), 'oi_multiplier': cfg['oi_multiplier']}
    symbols = cfg.get('symbols', ['NIFTY', 'BANKNIFTY', 'SBIN', 'RELIANCE'])
    rows = []
    for i, symbol in enumerate(symbols):
        rows.append({'Symbol': symbol, 'Future Contract': 'DEMO:' + symbol, 'Future Expiry': 'DEMO', 'Option Expiry': 'DEMO',
                     'Fut Close': 25000 / (i + 1), 'Fut OI': 1000000 + i * 200000 + tick * 100,
                     'Fut OI Chg': 50000 + tick * 100, 'Future': 'Long buildup', 'Fut View': 'Aggressive Bullish',
                     'ATM': 25000 / (i + 1), 'CE OI': 500000, 'CE OI Chg': -10000, 'CE Term': 'Short covering',
                     'CE View': 'Aggressive Bullish', 'PE OI': 600000, 'PE OI Chg': 10000,
                     'PE Term': 'Short buildup', 'PE View': 'Aggressive Bullish', 'Total CE OI': 2000000,
                     'Total PE OI': 2400000, 'PCR': 1.2, 'Net View': 'Strong Bullish', 'Observed At': stamp(),
                     'Next Future Expiry': 'DEMO NEXT', 'Next Fut Close': 25010 / (i + 1),
                     'Next Fut OI': 1050000 + i * 200000 + tick * 100, 'Next Fut OI Chg': -25000 - tick * 100,
                     'Next Future': 'Short buildup', 'Next Fut View': 'Aggressive Bearish',
                     'Next Option Expiry': 'DEMO NEXT', 'Next ATM': 25000 / (i + 1),
                     'Next CE OI': 510000, 'Next CE OI Chg': 15000, 'Next CE Term': 'Long buildup',
                     'Next CE View': 'Aggressive Bearish', 'Next Total CE OI': 2050000, 'Next Total CE OI Chg': 15000,
                     'Next PE OI': 590000, 'Next PE OI Chg': -12000, 'Next PE Term': 'Short covering',
                     'Next PE View': 'Bearish', 'Next Total PE OI': 2380000, 'Next Total PE OI Chg': -20000,
                     'Next PCR': 2380000 / 2050000, 'Next Net View': 'Strong Bearish'})
    return {'source': 'DEMO — synthetic', 'started_at': stamp(), 'collected_at': stamp(), 'rows': rows,
            'errors': [], 'comparison': 'Synthetic preview only', 'requested': len(rows)}


class Engine:
    def __init__(self, root, demo=False):
        self.root, self.demo = root, demo
        self.config_path = root / ('config.demo.json' if demo else 'config.json')
        source = self.config_path if self.config_path.exists() else root / 'config.json'
        self.config = validate(json.loads(source.read_text(encoding='utf-8-sig')))
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.wakes = {job: threading.Event() for job in JOBS}
        self.forced = set()
        self.status = {job: {'running': False, 'error': None, 'progress': '', 'last_success': None, 'next_run': None} for job in JOBS}
        self.store = Store(root / 'data' / ('demo.sqlite' if demo else 'market_data.sqlite'))
        self.money_alerts = MoneyAlerts(root, demo)
        self.fyers = Fyers(root, self.config['requests_per_second'], self.config['requests_per_minute'], self.config['requests_per_day'])
        self.nse = NSE(root)
        self.threads = []
        self.last_prune_day = None

    def get_config(self):
        with self.lock:
            return copy.deepcopy(self.config)

    def update_config(self, config):
        validate(config)
        with self.lock:
            temp = self.config_path.with_suffix('.tmp')
            temp.write_text(json.dumps(config, indent=2), encoding='utf-8')
            temp.replace(self.config_path)
            old = self.config
            self.config = copy.deepcopy(config)
            self.store.prune(self.config['retention_days'])
            self.fyers.rate = config['requests_per_second']
            self.fyers.per_minute = config['requests_per_minute']
            self.fyers.per_day = config['requests_per_day']
            for job in JOBS:
                if old[job] != config[job] or old['market'] != config['market']:
                    self.wakes[job].set()
        return self.get_config()

    def scope(self, job, cfg=None):
        cfg = cfg or self.get_config()
        if job == 'money':
            return money_scope({k: v for k, v in cfg['money'].items() if k != 'interval_seconds'})
        if job == 'oi':
            return json.dumps({'universe': cfg['oi']['universe'], 'symbols': sorted(cfg['oi']['symbols']) if cfg['oi']['universe'] == 'custom' else []}, sort_keys=True)
        return None

    def start(self):
        self.store.prune(self.config['retention_days'])
        for job in JOBS:
            thread = threading.Thread(target=self.worker, args=(job,), daemon=True, name=f'collector-{job}')
            thread.start()
            self.threads.append(thread)

    def trigger(self, job):
        if job not in JOBS:
            raise ValueError('Unknown collector.')
        with self.lock:
            if self.status[job]['running'] or job in self.forced:
                return False
            self.forced.add(job)
            self.wakes[job].set()
        return True

    def set_status(self, job, **values):
        with self.lock:
            self.status[job].update(values)

    def run_job(self, job, cfg):
        self.set_status(job, running=True, error=None, progress='Starting', started_at=stamp())
        with self.lock:
            if self.last_prune_day != now_ist().date():
                self.store.prune(cfg['retention_days'])
                self.last_prune_day = now_ist().date()
        scope = self.scope(job, cfg)
        progress = lambda msg: self.set_status(job, progress=msg)
        try:
            if job == 'nse':
                if self.demo:
                    payload = demo_payload(job, {})
                    payload['trade_date'] = now_ist().date().isoformat()
                    payloads, missing = [payload], []
                else:
                    payloads, missing = self.nse.collect({**cfg['nse'], 'min_price': cfg['oi']['min_price']}, now_ist().date(), progress)
                for payload in reversed(payloads):
                    existing = self.store.latest(job, payload['trade_date'])
                    has_expiry_sets = bool(existing and existing.get('rows')) and all('Next Future Expiry' in row and 'Next Option Expiry' in row for row in existing['rows'])
                    has_monthly_oi = bool(existing and existing.get('rows')) and all('Next Monthly Future Expiry' in row for row in existing['rows'])
                    if not existing or existing.get('min_price') != payload.get('min_price') or not has_expiry_sets or not has_monthly_oi:
                        self.store.save(job, payload['trade_date'], payload)
                latest = payloads[0]
                self.set_status(job, progress=f"Latest trading date: {latest['trade_date']}" + (' · newer bhavcopy not yet published' if missing else ''), trade_date=latest['trade_date'])
            else:
                previous = self.store.latest(job, scope)
                if self.demo:
                    payload = demo_payload(job, cfg[job])
                elif job == 'money':
                    money_cfg = dict(cfg[job])
                    alerts = self.money_alerts.status()
                    money_cfg['alert_window'] = {'near':alerts['near'], 'step':alerts['step']}
                    payload = self.fyers.collect_money(money_cfg)
                else:
                    payload = self.fyers.collect_oi(cfg[job], progress)
                add_changes(payload, previous, job)
                self.store.save(job, scope, payload)
                if job == 'money':
                    try:
                        threshold = self.money_alerts.status()['threshold']
                        self.money_alerts.track_movements(previous, payload, threshold)
                    except Exception:
                        self.set_status(job, error='Money data saved, but movement history could not be updated.')
                self.set_status(job, progress=f"{len(payload['rows'])} rows · {len(payload.get('errors', []))} incomplete/failed")
            self.set_status(job, last_success=stamp())
            if job != 'nse' and payload.get('errors'):
                self.set_status(job, error=f"Partial snapshot: {len(payload['errors'])} failed/incomplete rows. See collection status.")
                return False
            if job == 'money':
                try:
                    self.money_alerts.observe(payload, cfg['money'])
                except Exception:
                    self.set_status(job, error='Money data saved, but alert processing failed.')
            return True
        except Exception as exc:
            message = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else f'{type(exc).__name__}: collector failed; previous snapshot preserved.'
            self.set_status(job, error=message[:500], progress='Collection failed')
            return False
        finally:
            self.set_status(job, running=False)

    def worker(self, job):
        due = 0
        completed_day = None
        previous_cfg = None
        while not self.stop.is_set():
            cfg, now = self.get_config(), now_ist()
            fingerprint = json.dumps([cfg[job], cfg['market']], sort_keys=True)
            if previous_cfg != fingerprint:
                due, completed_day, previous_cfg = 0, None, fingerprint
            with self.lock:
                forced = job in self.forced
                self.forced.discard(job)
            # OI and bhavcopy are saved reports: only an explicit Collect request
            # may fetch them, including after startup or a configuration change.
            if job in ('oi', 'nse') or (job == 'money' and cfg['money']['interval_seconds'] == 0):
                if forced:
                    self.run_job(job, cfg)
                self.set_status(job, next_run='Manual only — click Collect now')
                self.wakes[job].wait(1)
                self.wakes[job].clear()
                continue
            daily = job == 'nse' or (job == 'oi' and cfg['oi']['mode'] == 'daily')
            enabled = job != 'nse' or cfg['nse']['enabled']
            eligible = self.demo or (trading_day(now, cfg) and (now.strftime('%H:%M') >= cfg[job]['daily_time'] if daily else in_session(now, cfg)))
            if forced or (enabled and eligible and time.monotonic() >= due and completed_day != now.date()):
                success = self.run_job(job, cfg)
                interval = cfg['nse']['retry_seconds'] if job == 'nse' else cfg[job]['interval_seconds']
                due = time.monotonic() + interval
                if success and daily:
                    if job != 'nse' or self.status[job].get('trade_date') == now.date().isoformat():
                        completed_day = now.date()
                self.set_status(job, next_run=(now_ist() + timedelta(seconds=interval)).isoformat() if completed_day != now.date() else 'Next trading day')
            elif not enabled:
                self.set_status(job, next_run='Disabled (manual refresh available)')
            elif not eligible:
                self.set_status(job, next_run='Waiting for scheduled market/session time (IST)')
            self.wakes[job].wait(1)
            self.wakes[job].clear()

    def snapshot(self, job, snapshot_id=None):
        if job == 'money':
            history = self.history(job)
            match = next((r for r in history if snapshot_id is None or r['id'] == snapshot_id), None)
            return self.store.latest(job, snapshot_id=match['id']) if match else None
        return self.store.latest(job, self.scope(job), snapshot_id)

    def history(self, job):
        if job == 'money':
            return self.store.money_history(self.get_config()['money'])
        return self.store.history(job, self.scope(job))

    def nse_month_history(self, month):
        return self.store.nse_month_history(month)

    def public_state(self):
        with self.lock:
            status = copy.deepcopy(self.status)
        return {'demo': self.demo, 'config': self.get_config(), 'nse_history_days': self.get_config()['nse']['history_days'], 'jobs': status, 'server_time': stamp(),
                'money_alerts': self.money_alerts.status(),
                'in_session': in_session(now_ist(), self.get_config()),
                'request_usage': 0 if self.demo else self.fyers.budget.usage()}
