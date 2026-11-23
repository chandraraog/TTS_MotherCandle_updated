"""Run: python OIDataV2/server.py --demo (or omit --demo for live collection)."""
import argparse
import asyncio
import base64
import hmac
import json
import os
import re
import ssl
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from engine import Engine, JOBS
from macd_job import MacdJob

ROOT = Path(__file__).resolve().parent
ASTRO_ROOT = ROOT.parent / 'astotiming' / 'astro_focus'
sys.path.insert(0, str(ASTRO_ROOT))
import app as astro_app_module


def make_handler(engine, macd=None, allowed_hosts=(), allowed_origins=(), username='', password=''):
    macd = macd or MacdJob(engine)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send(self, data, code=200, mime='application/json; charset=utf-8'):
            raw = json.dumps(data, allow_nan=False).encode('utf-8') if isinstance(data, (dict, list)) else data
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def local_host(self):
            host = self.headers.get('Host', '').lower()
            local_hosts = (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')
            return host in local_hosts or host in allowed_hosts

        def allowed_origin(self, origin):
            host = self.headers.get('Host', '').lower()
            if host in allowed_hosts:
                return origin in allowed_origins
            return origin in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}')

        def request_is_https(self):
            return isinstance(self.connection, ssl.SSLSocket) or self.headers.get('X-Forwarded-Proto', '').split(',')[0].strip().lower() == 'https'

        def authorized(self):
            if not username and not password:
                return True
            header = self.headers.get('Authorization', '')
            scheme, _, token = header.partition(' ')
            try:
                supplied = base64.b64decode(token, validate=True).decode('utf-8') if scheme.lower() == 'basic' else ''
            except (ValueError, UnicodeDecodeError):
                supplied = ''
            expected = f'{username}:{password}'
            if hmac.compare_digest(supplied, expected):
                return True
            raw = b'{"error":"Authentication required."}'
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="Chandra Market Desk", charset="UTF-8"')
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(raw)
            return False

        def forward_astro(self, path, query_string='', body=b''):
            headers = [(k.lower().encode('latin-1'), v.encode('latin-1')) for k, v in self.headers.items() if k.lower() not in ('host', 'connection')]
            scope = {
                'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'},
                'http_version': '1.1', 'method': self.command, 'scheme': 'https' if self.request_is_https() else 'http',
                'path': path or '/', 'raw_path': (path or '/').encode('utf-8'),
                'query_string': query_string.encode('latin-1'), 'root_path': '',
                'headers': headers, 'client': self.client_address,
                'server': self.server.server_address,
            }
            sent = []
            received = False
            async def receive():
                nonlocal received
                if not received:
                    received = True
                    return {'type': 'http.request', 'body': body, 'more_body': False}
                return {'type': 'http.disconnect'}
            async def send(message):
                sent.append(message)
            asyncio.run(astro_app_module.app(scope, receive, send))
            start = next(m for m in sent if m['type'] == 'http.response.start')
            payload = b''.join(m.get('body', b'') for m in sent if m['type'] == 'http.response.body')
            self.send_response(start['status'])
            for key, value in start.get('headers', []):
                name, val = key.decode('latin-1'), value.decode('latin-1')
                if name.lower() not in ('connection', 'transfer-encoding'):
                    self.send_header(name, val)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if not self.local_host():
                self.send({'error': 'Host is not allowed. Configure --allowed-host for this address.'}, 403)
                return
            if self.headers.get('Host', '').lower() in allowed_hosts and not self.request_is_https():
                self.send({'error': 'HTTPS is required for remote access.'}, 400)
                return
            if not self.authorized():
                return
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == '/astotiming' or parsed.path.startswith('/astotiming/'):
                self.forward_astro(parsed.path[len('/astotiming'):] or '/', parsed.query)
                return
            try:
                if parsed.path == '/api/state':
                    self.send(engine.public_state())
                elif parsed.path == '/api/money/movements':
                    symbol = query.get('symbol', [''])[0].strip().upper()
                    expiry = query.get('expiry', [''])[0].strip()
                    if not re.fullmatch(r'(?:NSE|BSE|MCX):[A-Z0-9&_.-]{1,60}', symbol):
                        raise ValueError('Enter a valid Money instrument.')
                    self.send({'events': engine.money_alerts.movement_history(symbol, expiry)})
                elif parsed.path == '/api/macd/status':
                    self.send(macd.status())
                elif parsed.path == '/api/macd':
                    report = macd.report_path(query.get('source', ['watchlist'])[0])
                    if not report.exists():
                        self.send({'error': 'No completed MACD report yet. Use Run scan; progress appears above.'}, 404)
                        return
                    content = report.read_text(encoding='utf-8-sig')
                    match = re.search(r'\blet\s+D\s*=\s*', content)
                    if not match:
                        raise ValueError('MACD report data format not recognized.')
                    data, _ = json.JSONDecoder().raw_decode(content[match.end():])
                    if not isinstance(data, dict) or not all(isinstance(data.get(k), list) for k in ('buy','sell','order')):
                        raise ValueError('MACD report data is incomplete.')
                    self.send({'report': data, 'modified_at': datetime.fromtimestamp(report.stat().st_mtime, timezone.utc).isoformat()})
                elif parsed.path in ('/api/snapshot', '/api/history'):
                    job = query.get('job', ['money'])[0]
                    if job not in JOBS:
                        raise ValueError('Unknown collector.')
                    if parsed.path.endswith('history'):
                        month = query.get('month', [''])[0]
                        if job == 'nse' and re.fullmatch(r'\d{4}-\d{2}', month):
                            self.send(engine.nse_month_history(month))
                        else:
                            self.send(engine.history(job))
                    else:
                        snapshot_id = int(query['id'][0]) if query.get('id') else None
                        self.send({'snapshot': engine.snapshot(job, snapshot_id)})
                else:
                    assets = {'/': ('money.html', 'text/html'), '/money.html': ('money.html', 'text/html'),
                              '/oi.html': ('oi.html', 'text/html'), '/macd.html': ('macd.html', 'text/html'), '/macd.js': ('macd.js', 'text/javascript'), '/app.js': ('app.js', 'text/javascript'),
                              '/style.css': ('style.css', 'text/css')}
                    if parsed.path not in assets:
                        self.send({'error': 'Not found'}, 404)
                        return
                    name, mime = assets[parsed.path]
                    self.send((ROOT / 'web' / name).read_bytes(), mime=mime + '; charset=utf-8')
            except (ValueError, KeyError) as exc:
                self.send({'error': str(exc)}, 400)

        def do_POST(self):
            origin = self.headers.get('Origin')
            request_host = self.headers.get('Host', '').lower()
            if request_host in allowed_hosts and not self.request_is_https():
                self.send({'error': 'HTTPS is required for remote access.'}, 400)
                return
            if not self.local_host() or (request_host in allowed_hosts and not origin) or (origin and not self.allowed_origin(origin)):
                self.send({'error': 'Cross-origin requests are not allowed.'}, 403)
                return
            if not self.authorized():
                return
            if self.path.startswith('/astotiming/'):
                length = int(self.headers.get('Content-Length', '0'))
                if length > 65536:
                    self.send({'error': 'Invalid request size.'}, 400)
                    return
                self.forward_astro(urlparse(self.path).path[len('/astotiming'):] or '/', urlparse(self.path).query, self.rfile.read(length))
                return
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                self.send({'error': 'JSON required.'}, 415)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536:
                    raise ValueError('Invalid request size.')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Expected a JSON object.')
                if self.path == '/api/config':
                    self.send({'config': engine.update_config(body)})
                elif self.path == '/api/money/expiries':
                    symbol = body.get('symbol', '')
                    if not isinstance(symbol, str) or not re.fullmatch(r'(?:NSE|BSE|MCX):[A-Z0-9&_.-]{1,60}', symbol):
                        raise ValueError('Enter a valid FYERS exchange:symbol identifier.')
                    if engine.demo:
                        saved = engine.snapshot('money')
                        expiries = saved.get('expiries', []) if saved and saved.get('symbol') == symbol else []
                    else:
                        expiries = engine.fyers.chain(symbol, 1).get('expiryData', [])
                    self.send({'symbol': symbol, 'expiries': sorted(expiries, key=lambda e: int(e['expiry']))})
                elif self.path == '/api/money/movements/clear':
                    self.send({'deleted': engine.money_alerts.clear_movement_history()})
                elif self.path == '/api/money/movement-settings':
                    self.send(engine.money_alerts.set_movement_tracking(body))
                elif self.path == '/api/money/alerts':
                    self.send(engine.money_alerts.configure(body))
                elif self.path == '/api/money/send-alert':
                    ident=body.get('snapshot_id')
                    if type(ident) is not int or ident<=0:
                        raise ValueError('Select a saved Money snapshot first.')
                    snapshot=engine.snapshot('money',ident)
                    if not snapshot:
                        raise ValueError('That snapshot is unavailable for the saved instrument settings.')
                    self.send(engine.money_alerts.send_now(snapshot,body))
                elif self.path == '/api/macd/run':
                    self.send({'started': macd.start(body.get('source'), body.get('min_price')), 'status': macd.status()}, 202)
                elif self.path == '/api/macd/settings':
                    self.send(macd.automation.configure(body))
                elif self.path == '/api/refresh':
                    self.send({'queued': engine.trigger(body.get('job'))}, 202)
                else:
                    self.send({'error': 'Not found'}, 404)
            except (ValueError, KeyError, TypeError, RuntimeError) as exc:
                self.send({'error': str(exc)}, 400)
    return Handler


def main():
    parser = argparse.ArgumentParser(description='Money, Open Interest, MACD and Astro dashboard server')
    parser.add_argument('--demo', action='store_true', help='Synthetic data, isolated database, no external requests')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--host', default='127.0.0.1', help='Bind address; defaults to localhost')
    parser.add_argument('--allowed-host', action='append', default=[], help='Allowed Host header; repeat for each host, include port when nonstandard')
    parser.add_argument('--allowed-origin', action='append', default=[], help='Allowed browser Origin URL; repeat for each HTTPS origin')
    parser.add_argument('--tls-cert', help='TLS certificate PEM file (required when binding to a non-local address)')
    parser.add_argument('--tls-key', help='TLS private key PEM file (required when binding to a non-local address)')
    parser.add_argument('--collect-once', choices=['money', 'oi', 'nse', 'all'], help='Collect and save once without starting the web server')
    parser.add_argument('--no-macd', action='store_true', help='Compatibility option: startup never launches a MACD scan')
    args = parser.parse_args()
    is_loopback = args.host in ('127.0.0.1', 'localhost', '::1')
    username = os.environ.get('DASHBOARD_USERNAME', '')
    password = os.environ.get('DASHBOARD_PASSWORD', '')
    remote_enabled = not is_loopback or bool(args.allowed_host)
    if bool(username) != bool(password):
        parser.error('Set both DASHBOARD_USERNAME and DASHBOARD_PASSWORD, or neither.')
    if remote_enabled and (not username or not password):
        parser.error('Remote access requires DASHBOARD_USERNAME and DASHBOARD_PASSWORD environment variables.')
    if remote_enabled and (not args.allowed_origin or any(not origin.startswith('https://') for origin in args.allowed_origin)):
        parser.error('Remote access requires one or more --allowed-origin values using https://.')
    if not is_loopback and (not args.tls_cert or not args.tls_key):
        parser.error('Binding to a network interface requires --tls-cert and --tls-key; use an HTTPS reverse proxy for TLS termination instead.')
    if not is_loopback and not args.allowed_host:
        parser.error('Network binding requires at least one --allowed-host value.')
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error('Provide both --tls-cert and --tls-key.')
    engine = Engine(ROOT, demo=args.demo)
    if args.collect_once:
        jobs = JOBS if args.collect_once == 'all' else (args.collect_once,)
        failed = False
        for job in jobs:
            print(f'Collecting {job}...', flush=True)
            success = engine.run_job(job, engine.get_config())
            print(json.dumps(engine.status[job]), flush=True)
            failed = failed or not success
        raise SystemExit(1 if failed else 0)
    macd = MacdJob(engine)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(engine, macd, tuple(h.lower() for h in args.allowed_host), tuple(args.allowed_origin), username, password))
    if args.tls_cert:
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
        tls_context.load_cert_chain(args.tls_cert, args.tls_key)
        server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    server.daemon_threads = True
    engine.start()
    macd.automation.start()
    scheme = 'https' if args.tls_cert else 'http'
    display_host = '127.0.0.1' if is_loopback else args.host
    print(f"{'DEMO' if args.demo else 'LIVE'} dashboard: {scheme}://{display_host}:{args.port}/money.html", flush=True)
    print(f'MACD dashboard: {scheme}://{display_host}:{args.port}/macd.html', flush=True)
    print(f'OI dashboard: {scheme}://{display_host}:{args.port}/oi.html', flush=True)
    print(f'Astro Timing dashboard: {scheme}://{display_host}:{args.port}/astotiming/', flush=True)
    print('Press Ctrl+C to stop. Automatic live collection observes configured IST session times.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop.set()
        for wake in engine.wakes.values():
            wake.set()
        server.server_close()


if __name__ == '__main__':
    main()
