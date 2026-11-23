"""Single background MACD job, shared by startup and UI requests."""
import importlib.util
import json
import math
import threading
from datetime import datetime, timezone
from macd_automation import MacdAutomation


class MacdJob:
    def __init__(self, engine):
        self.engine = engine
        self.lock = threading.Lock()
        self.thread = None
        self.settings_path = engine.root / 'data' / 'macd_settings.json'
        self.selected_source = 'watchlist'
        self.min_price = 1000.0
        try:
            value = json.loads(self.settings_path.read_text(encoding='utf-8')).get('source')
            self.validate_source(value)
            self.selected_source = value
            self.min_price = self.validate_price(json.loads(self.settings_path.read_text(encoding='utf-8')).get('min_price', 1000))
        except (OSError, ValueError, TypeError):
            pass
        self.state = {'running': False, 'progress': 'Not started', 'error': None,
                      'started_at': None, 'finished_at': None, 'enabled': not engine.demo, 'source': self.selected_source, 'min_price': self.min_price}
        self.automation = MacdAutomation(self)

    @staticmethod
    def validate_price(value):
        try:
            if isinstance(value, bool):
                raise ValueError
            price = float(value)
            if not math.isfinite(price) or price < 0:
                raise ValueError
            return price
        except (TypeError, ValueError):
            raise ValueError('Scan price must be a finite number of zero or more.') from None

    @staticmethod
    def validate_source(source):
        if source not in ('watchlist', 'bhavcopy'):
            raise ValueError('Select Watchlist or Bhavcopy F&O.')

    def report_path(self, source):
        self.validate_source(source)
        name = 'macd_scan_report.html' if source == 'watchlist' else 'macd_bhavcopy_report.html'
        return self.engine.root / name

    def resolve_bhavcopy(self):
        daily = self.engine.store.latest('nse')
        if not daily or not daily.get('rows'):
            raise ValueError('No saved NSE bhavcopy. Collect NSE daily data on the OI tab first.')
        contracts = self.engine.fyers.master()
        mapping = {r['underlying_name']: r['underlying'] for r in contracts if r['symbol'].endswith('FUT')}
        names = sorted({row['Symbol'] for row in daily['rows']})
        missing = [name for name in names if name not in mapping]
        if missing:
            raise ValueError('Bhavcopy symbols missing from current Fyers master: ' + ', '.join(missing) + '. Refresh NSE daily data and retry.')
        symbols = sorted({mapping[name] for name in names})
        return symbols, {'universe': 'bhavcopy', 'universe_label': 'Bhavcopy F&O',
                         'bhavcopy_date': daily['trade_date'], 'candle_source': 'FYERS',
                         'universe_count': len(symbols)}

    def status(self):
        with self.lock:
            result = dict(self.state)
        result['automation'] = self.automation.status()
        return result

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def start(self, source=None, min_price=None):
        source = self.selected_source if source is None else source
        self.validate_source(source)
        min_price = self.min_price if min_price is None else self.validate_price(min_price)
        with self.lock:
            if self.engine.demo:
                raise ValueError('MACD broker scans are disabled in demo mode. Start the live server.')
            if self.state['running']:
                return False
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'source': source, 'min_price': min_price}), encoding='utf-8')
            temporary.replace(self.settings_path)
            self.selected_source = source
            self.min_price = min_price
            self.state.update(running=True, progress='Starting scanner…', error=None, source=source, min_price=min_price,
                              started_at=datetime.now(timezone.utc).isoformat(), finished_at=None)
            self.thread = threading.Thread(target=self.run, name='macd-scanner', daemon=True)
            self.automation.started(source)
            self.thread.start()
            return True

    def run(self):
        try:
            path = self.engine.root / 'macd_mtf_scanner.py'
            spec = importlib.util.spec_from_file_location('dashboard_macd_scanner', path)
            scanner = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(scanner)
            scanner.get_client = lambda: scanner.HistoryClient(self.engine.fyers, self.engine.stop)
            scanner.PROGRESS_CALLBACK = lambda message: self.update(progress=message)
            source = self.status()['source']
            if source == 'watchlist':
                scanner.OBSERVATION_CALLBACK = self.automation.observe
            symbols, metadata = (self.resolve_bhavcopy() if source == 'bhavcopy' else
                                 (None, {'universe': 'watchlist', 'universe_label': 'Watchlist', 'candle_source': 'FYERS'}))
            scanner.REPORT_METADATA = metadata
            metadata['scan_min_price'] = self.status()['min_price']
            scanner.OUTPUT_HTML = str(self.report_path(source))
            scanner.OUTPUT_CSV = str(self.engine.root / ('macd_scan_result.csv' if source == 'watchlist' else 'macd_bhavcopy_result.csv'))
            scanner.run(scanner.MIN_TF_MATCH, scanner.ANCHOR, scanner.SHEET_SOURCE,
                        scanner.FINAL_MIN, open_browser=False, min_price=self.status()['min_price'], symbols_override=symbols)
            errors = (scanner.LAST_DATA or {}).get('errors', [])
            self.update(progress='Report updated' if not errors else f'Report updated · {len(errors)} symbols failed',
                        error='; '.join(errors[:3]) if errors else None)
        except (Exception, SystemExit) as exc:
            self.update(progress='Scan stopped; previous report preserved', error=str(exc)[:600] or type(exc).__name__)
        finally:
            self.update(running=False, finished_at=datetime.now(timezone.utc).isoformat())
