"""Read-only FYERS HTTP adapter. No SDK logging and no trading endpoints."""
import csv
import io
import json
import math
import os
import threading
import time
from datetime import datetime, timezone

import requests
from dotenv import dotenv_values

from request_budget import RequestBudget
from calculations import number, difference, money, interpretation, net_view, bias

INDEX = {"NIFTY": "NSE:NIFTY50-INDEX", "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
         "FINNIFTY": "NSE:FINNIFTY-INDEX", "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
         "NIFTYNXT50": "NSE:NIFTYNXT50-INDEX"}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def money_scope(cfg):
    return json.dumps(cfg, sort_keys=True)


class Fyers:
    def __init__(self, root, rate=0.5, per_minute=40, per_day=4500):
        self.root, self.rate = root, rate
        self.per_minute, self.per_day = per_minute, per_day
        self.budget = RequestBudget(root / "data" / "request_usage.sqlite")
        self.lock = threading.Lock()
        self.master_lock = threading.Lock()
        self.last_call = 0
        self.contracts = []
        self.master_day = ""

    def call(self, endpoint, params):
        # Reload credentials for each request so a refreshed .env token is picked up.
        creds = {**dotenv_values(self.root.parent / ".env"),
                 **dotenv_values(self.root / ".env"), **os.environ}
        client, token = creds.get("FYERS_CLIENT_ID", ""), creds.get("FYERS_ACCESS_TOKEN", "")
        if not client or not token:
            raise RuntimeError("Set FYERS_CLIENT_ID and FYERS_ACCESS_TOKEN in .env, then retry.")
        auth = token if token.startswith(client + ":") else client + ":" + token
        with self.lock:
            time.sleep(max(0, 1 / self.rate - (time.monotonic() - self.last_call)))
            while True:
                delay = self.budget.reserve(self.per_minute, self.per_day)
                if not delay:
                    break
                time.sleep(delay)
            self.last_call = time.monotonic()
            try:
                response = requests.get("https://api-t1.fyers.in/data/" + endpoint,
                                        params=params, headers={"Authorization": auth}, timeout=(10, 25))
            except requests.RequestException:
                raise RuntimeError("FYERS network request failed or timed out.") from None
        if response.status_code in (401, 403):
            raise RuntimeError("FYERS authentication/permission failed. Refresh the access token.")
        if response.status_code == 429:
            self.budget.cooldown()
            raise RuntimeError("FYERS rate limit reached. Reduce request rate / increase collection intervals.")
        if not response.ok:
            raise RuntimeError(f"FYERS HTTP error {response.status_code}.")
        try:
            result = response.json()
        except ValueError:
            raise RuntimeError("FYERS returned invalid JSON.") from None
        if result.get("s") != "ok" and (result.get("code") in (-16, -17) or "token" in str(result.get("message", "")).lower()):
            raise RuntimeError("FYERS authentication failed. Refresh the access token.")
        if result.get("code") in (429, -429, -360):
            self.budget.cooldown()
            raise RuntimeError("FYERS rate limit reached. Increase collection intervals.")
        if result.get("s") != "ok":
            # Do not echo a raw upstream response: it might contain account information.
            raise RuntimeError(f"FYERS rejected {endpoint} (code {result.get('code', 'unknown')}). Check symbol, token and permissions.")
        return result

    def master(self):
        with self.master_lock:
            day = datetime.now(timezone.utc).date().isoformat()
            if self.master_day == day:
                return self.contracts
            cache = self.root / "data" / "NSE_FO.csv"
            if cache.exists() and datetime.fromtimestamp(cache.stat().st_mtime, timezone.utc).date().isoformat() == day:
                text = cache.read_text(encoding="utf-8")
            else:
                try:
                    r = requests.get("https://public.fyers.in/sym_details/NSE_FO.csv", timeout=(10, 40))
                    r.raise_for_status()
                    text = r.text
                except requests.RequestException:
                    raise RuntimeError("Could not refresh FYERS instrument master.") from None
            # Resolve underlying tokens through the cash/index master, including newly
            # introduced indices; do not assume every non-NIFTY name is an equity.
            cash_cache = self.root / "data" / "NSE_CM.csv"
            if cash_cache.exists() and datetime.fromtimestamp(cash_cache.stat().st_mtime, timezone.utc).date().isoformat() == day:
                cash_text = cash_cache.read_text(encoding="utf-8")
            else:
                try:
                    cash_response = requests.get("https://public.fyers.in/sym_details/NSE_CM.csv", timeout=(10, 40))
                    cash_response.raise_for_status()
                    cash_text = cash_response.text
                except requests.RequestException:
                    raise RuntimeError("Could not refresh FYERS underlying-symbol master.") from None
            underlying_symbols = {r[0]: r[9] for r in csv.reader(io.StringIO(cash_text)) if len(r) > 9 and r[9].startswith("NSE:")}
            if not underlying_symbols:
                raise RuntimeError("FYERS underlying-symbol master has no valid symbols.")
            cash_cache.parent.mkdir(parents=True, exist_ok=True)
            cash_cache.write_text(cash_text, encoding="utf-8")
            contracts = []
            for row in csv.reader(io.StringIO(text)):
                if len(row) < 17 or not row[9].startswith("NSE:"):
                    continue
                expiry = number(row[8])
                if not expiry or expiry < time.time():
                    continue
                under = row[13]
                contracts.append({"symbol": row[9], "underlying_name": under, "underlying": underlying_symbols.get(row[17] if len(row) > 17 else "", INDEX.get(under, f"NSE:{under}-EQ")),
                                  "expiry": expiry, "lot_size": number(row[3]),
                                  "type": row[16], "strike": number(row[15])})
            if not contracts:
                raise RuntimeError("FYERS instrument master has no valid active contracts; check its schema.")
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(text, encoding="utf-8")
            self.contracts, self.master_day = contracts, day
            return contracts

    def universe(self):
        return sorted({r["underlying"] for r in self.master() if r["symbol"].endswith("FUT")})

    def depth(self, symbol):
        result = self.call("depth", {"symbol": symbol, "ohlcv_flag": 1})
        row = result.get("d", {}).get(symbol)
        if not isinstance(row, dict):
            raise RuntimeError("FYERS depth response is missing the requested contract.")
        return row

    def chain(self, symbol, strikes, expiry=""):
        resolved = self.resolve_money_symbol(symbol)
        result = self.call("options-chain-v3", {"symbol": resolved, "strikecount": strikes, "timestamp": expiry})
        data = result.get("data", {})
        if not data.get("optionsChain"):
            raise RuntimeError("No option chain returned for this instrument / expiry.")
        data['resolved_symbol'] = resolved
        return data

    def resolve_money_symbol(self, symbol):
        if symbol != 'MCX:NATURALGAS':
            return symbol
        cache = self.root / 'data' / 'MCX_COM.csv'
        with self.master_lock:
            if not cache.exists() or time.time() - cache.stat().st_mtime > 86400:
                try:
                    response = requests.get('https://public.fyers.in/sym_details/MCX_COM.csv', timeout=(10,30))
                    response.raise_for_status()
                except requests.RequestException:
                    raise RuntimeError('Could not load MCX symbols. Retry or enter an exact FYERS futures symbol.') from None
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(response.text, encoding='utf-8')
            contracts = []
            for row in csv.reader(io.StringIO(cache.read_text(encoding='utf-8'))):
                if len(row)>9 and row[9].startswith('MCX:NATURALGAS') and row[9].endswith('FUT'):
                    expiry = number(row[8])
                    if expiry and expiry>time.time():
                        contracts.append((expiry,row[9]))
        if not contracts:
            raise RuntimeError('No active Natural Gas futures found in the FYERS master.')
        return min(contracts)[1]

    def collect_money(self, cfg):
        started = stamp()
        chain = self.chain(cfg["symbol"], cfg["strikes"], cfg["expiry"])
        window = cfg.get('alert_window')
        if window:
            underlying = chain.get('resolved_symbol', cfg['symbol'])
            spot = next((number(r.get('ltp')) for r in chain['optionsChain'] if r.get('symbol') == underlying), None)
            strikes = sorted({float(r['strike_price']) for r in chain['optionsChain'] if r.get('option_type') in ('CE','PE')})
            if spot is not None and len(strikes)>1:
                spacing = min(b-a for a,b in zip(strikes,strikes[1:]) if b>a)
                atm = math.floor(spot/window['step']+0.5)*window['step']
                targets = {round(atm+i*window['step'],8) for i in range(-window['near'],window['near']+1)}
                if not targets.issubset(set(strikes)):
                    count = min(50,max(cfg['strikes'],math.ceil((window['near']*window['step']+abs(atm-spot))/spacing)+1))
                    if count>cfg['strikes']:
                        chain = self.chain(cfg['symbol'],count,cfg['expiry'])
        expiries = sorted(chain.get("expiryData", []), key=lambda e: int(e["expiry"]))
        selected = next((e for e in expiries if str(e["expiry"]) == cfg["expiry"]), None) if cfg["expiry"] else (expiries[0] if expiries else None)
        if selected is None:
            raise RuntimeError("Selected expiry is unavailable. Choose Nearest expiry and retry.")
        rows, errors = [], []
        spot = next((number(r.get("ltp")) for r in chain["optionsChain"] if r.get("symbol") == chain.get('resolved_symbol',cfg["symbol"])), None)
        for item in chain["optionsChain"]:
            if item.get("option_type") not in ("CE", "PE"):
                continue
            row = {"symbol": item["symbol"], "strike": item["strike_price"], "type": item["option_type"], "expiry": selected["date"]}
            try:
                d = self.depth(item["symbol"])
                if d.get("oiflag") not in (True, "true", 1):
                    raise RuntimeError("OI is unavailable for this contract.")
                row.update(oi=number(d.get("oi")), previous_oi=number(d.get("pdoi")), atp=number(d.get("atp")),
                           ltp=number(d.get("ltp")), last_trade_time=d.get("ltt"), observed_at=stamp())
                row["oi_change"] = difference(row["oi"], row["previous_oi"])
                row["money"] = money(row["oi"], row["previous_oi"], row["atp"], cfg["oi_multiplier"])
                if row["money"] is None:
                    row["error"] = "Missing OI / previous OI / valid ATP"
            except RuntimeError as exc:
                row["error"] = str(exc)
                # An account-wide failure should abort instead of retrying every contract.
                if any(x in str(exc) for x in ("authentication", "rate limit", "daily request budget", "Set FYERS")):
                    raise
            if row.get("error"):
                errors.append({"symbol": row["symbol"], "error": row["error"]})
            rows.append(row)
        rows.sort(key=lambda r: (r["strike"], r["type"]))
        if not rows or all(r.get("money") is None for r in rows):
            raise RuntimeError("No valid Money rows. Check OI availability, ATP and FYERS credentials.")
        totals = {}
        for side in ("CE", "PE"):
            values = [r.get("money") for r in rows if r["type"] == side]
            totals[side] = sum(values) if values and all(v is not None for v in values) else None
        return {"source": "FYERS", "started_at": started, "collected_at": stamp(), "symbol": cfg["symbol"],
                "expiry": selected, "expiries": expiries, "spot": spot, "rows": rows, "errors": errors,
                "totals": totals, "bias": bias(totals["CE"], totals["PE"]), "oi_multiplier": cfg["oi_multiplier"]}

    def collect_oi(self, cfg, progress=lambda message: None):
        started = stamp()
        contracts = self.master()
        symbols = self.universe() if cfg["universe"] == "all" else cfg["symbols"]
        rows, errors = [], []
        for index, symbol in enumerate(symbols):
            progress(f"{index + 1}/{len(symbols)} · {symbol}")
            try:
                minimum = cfg.get("min_price", 1000)
                if minimum > 0:
                    spot = number(self.depth(symbol).get("ltp"))
                    if spot is None:
                        raise RuntimeError("Underlying price unavailable; skipped price screening.")
                    if spot <= minimum:
                        continue
                futures = sorted((c for c in contracts if c["underlying"] == symbol and c["symbol"].endswith("FUT")), key=lambda c: c["expiry"])
                if not futures:
                    raise RuntimeError("No active futures contract in instrument master.")
                future = futures[0]
                d = self.depth(future["symbol"])
                if d.get("oiflag") not in (True, "true", 1):
                    raise RuntimeError("Futures OI unavailable.")
                chain = self.chain(symbol, 50)
                options = [r for r in chain["optionsChain"] if r.get("option_type") in ("CE", "PE")]
                price = number(d.get("ltp"))
                if price is None or not options:
                    raise RuntimeError("Missing futures price or options.")
                atm = min({r["strike_price"] for r in options}, key=lambda k: abs(k - price))
                change = difference(d.get("oi"), d.get("pdoi"))
                term, view = interpretation("FUT", d.get("ch"), change)
                row = {"Symbol": symbol, "Future Contract": future["symbol"], "Future Expiry": datetime.fromtimestamp(future["expiry"], timezone.utc).date().isoformat(),
                       "Fut Close": price, "Fut OI": number(d.get("oi")), "Fut OI Chg": change,
                       "Future": term, "Fut View": view, "ATM": atm, "Observed At": stamp()}
                expiries = sorted(chain.get("expiryData", []), key=lambda e: int(e["expiry"]))
                row["Option Expiry"] = expiries[0]["date"] if expiries else "Unknown"
                views = [view]
                for side in ("CE", "PE"):
                    item = next((r for r in options if r["strike_price"] == atm and r["option_type"] == side), {})
                    oi_change = difference(item.get("oi"), item.get("prev_oi"))
                    term, view = interpretation(side, item.get("ltpch"), oi_change)
                    row.update({f"{side} OI": number(item.get("oi")), f"{side} OI Chg": oi_change,
                                f"{side} Term": term, f"{side} View": view})
                    views.append(view)
                row["Total CE OI"], row["Total PE OI"] = number(chain.get("callOi")), number(chain.get("putOi"))
                row["PCR"] = row["Total PE OI"] / row["Total CE OI"] if row["Total CE OI"] and row["Total PE OI"] is not None else None
                row["Net View"] = net_view(views)
                rows.append(row)
            except RuntimeError as exc:
                errors.append({"symbol": symbol, "error": str(exc)})
                if any(x in str(exc) for x in ("authentication", "rate limit", "daily request budget", "Set FYERS")):
                    if not rows:
                        raise
                    errors.extend({"symbol": remaining, "error": "Not attempted: scan stopped after " + str(exc)} for remaining in symbols[index + 1:])
                    break
        if not rows and errors:
            raise RuntimeError("No OI observations collected. " + (errors[0]["error"] if errors else "Empty universe."))
        return {"source": "FYERS", "started_at": started, "collected_at": stamp(), "rows": rows, "errors": errors,
                "requested": len(symbols), "min_price": cfg.get("min_price", 1000), "comparison": "Previous trading day; nearest futures and nearest options expiry (may differ). ATM nearest futures price within ±50 spot strikes."}
