r"""
    
python .\macd_mtf_scanner.py --source "https://docs.google.com/spreadsheets/d/1pVoTYVOG2cIQaA-KHHQ3BlrZxdsDlHB_AVN7N6j5L9g/edit?usp=sharing" --min-price 1000 --final 4 --every 15

MACD Multi-Timeframe Scanner (Fyers)
Port of the Pine indicator "MACD|RSI|STOCH" -> MACD Trend column.

Strong Bull : MACD >= Signal  AND MACD > 0
Strong Bear : MACD <  Signal  AND MACD < 0
Recovering  : MACD >= Signal  AND MACD <= 0
Weakening   : MACD <  Signal  AND MACD >= 0

TOP-DOWN alignment: starts at the highest timeframe (ANCHOR, default Monthly).
It must be Strong Bull (or Strong Bear); then each lower timeframe is checked
in order (M > W > D > 4H > 1H > 15m > 5m) and the chain continues only while
they show the same state. Lists stocks whose chain is >= MIN_TF_MATCH long.
Lower-timeframe-only alignment is ignored.

Setup
-----
    pip install fyers-apiv3 pandas python-dotenv

.env file:
    FYERS_CLIENT_ID=XXXXXXXXXX-100
    FYERS_SECRET_KEY=xxxxxxxx
    FYERS_REDIRECT_URI=http://127.0.0.1
    FYERS_ACCESS_TOKEN=            # filled automatically by --login

Usage
-----
    python macd_mtf_scanner.py --login      # once per day, gets access token
    python macd_mtf_scanner.py              # run scan on stocks.txt
    python macd_mtf_scanner.py --min 3      # chain must be 3+ timeframes long
    python macd_mtf_scanner.py --source "<google sheet link>" --final 4   # only stocks with Final > 4%
    python macd_mtf_scanner.py --min-price 200   # ignore cheap stocks
    python macd_mtf_scanner.py --every 15   # repeat every 15 min during market hours (60 = hourly)
    python macd_mtf_scanner.py --anchor W   # start the chain at Weekly instead of Monthly
"""

import os
from pathlib import Path
import re
import sys
import time
import argparse

# Redirected Windows terminals may not support the report emoji.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="backslashreplace")
import json
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import webbrowser
from datetime import datetime, timedelta, timezone

import pandas as pd
from dotenv import load_dotenv, set_key
from fyers_apiv3 import fyersModel

# ==========================================================
# SETTINGS  (match your Pine inputs)
# ==========================================================
BASE = Path(__file__).resolve().parent
PROJECT = BASE.parent
PROGRESS_CALLBACK = None
OBSERVATION_CALLBACK = None
REPORT_METADATA = {}
FAST, SLOW, SIGNAL = 5, 35, 5

# Timeframes to evaluate. Fyers has no native W / M, so those are
# built from daily candles.
# Highest -> lowest. Chain is evaluated in this order.
HTF_ORDER = ["M", "W", "D", "240", "60", "15", "5"]
ANCHOR = "M"                # highest timeframe the chain must start from

MIN_TF_MATCH = 2            # "any two different timeframes"
STOCKS_FILE = str(PROJECT / "stocks.txt")  # used only when no sheet source is given

# Google Sheet source (paste your sheet link here once, or use --source)
SHEET_SOURCE = ""           # Google Sheet URL, or path to a downloaded .csv / .xlsx
NAME_COL = "Name"           # column with symbols like NSE:INFY
PRICE_COL = "Price"         # sheet column used to pre-filter by price (saves API calls)
FINAL_COL = "Final"         # column used for the filter
MIN_PRICE = 0               # skip stocks priced below this (override with --min-price)
FINAL_MIN = None            # None = scan every stock in the sheet; set e.g. 4.0 (or use --final 4) to keep Final > 4%
OUTPUT_CSV = str(BASE / "macd_scan_result.csv")
OUTPUT_HTML = str(BASE / "macd_scan_report.html")
SLOT_DELAY = 10             # seconds after each 15m/60m mark, so the candle has closed
MARKET_ONLY = True          # in --every mode, only scan Mon-Fri 09:15-15:35 IST
DASH_PORT = 8770            # local dashboard port used by --every (http://127.0.0.1:8765/)
OPEN_IF_IDLE_SECS = 120     # open a tab only if no open page has checked in for this long
OPEN_BROWSER = True         # open the HTML report when the scan finishes
ENV_FILE = str(PROJECT / ".env")

# How much history to pull per timeframe (days). Enough for EMA warm-up.
LOOKBACK_DAYS = {
    "5": 30,
    "15": 60,
    "60": 100,
    "240": 200,
    "D": 365 * 5,           # Standard plan daily history; also feeds Weekly and Monthly
}
CHUNK_DAYS = {"5": 90, "15": 90, "60": 90, "240": 90, "D": 360}  # Fyers per-call limits
API_DELAY = 0.35            # seconds between calls (Fyers limit ~200/min)

TF_LABEL = {"5": "5m", "15": "15m", "60": "1H", "240": "4H",
            "D": "D", "W": "W", "M": "M"}  # short labels keep the report tidy

# ==========================================================
# AUTH
# ==========================================================
load_dotenv(ENV_FILE)
CLIENT_ID = os.getenv("FYERS_CLIENT_ID")
SECRET_KEY = os.getenv("FYERS_SECRET_KEY")
REDIRECT_URI = os.getenv("FYERS_REDIRECT_URI", "http://127.0.0.1")


def login():
    """Generate today's access token and save it to .env"""
    session = fyersModel.SessionModel(
        client_id=CLIENT_ID,
        secret_key=SECRET_KEY,
        redirect_uri=REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code",
    )
    print("\n1) Open this URL in your browser and log in:\n")
    print(session.generate_authcode())
    print("\n2) After login you are redirected to a URL containing  auth_code=...")
    auth_code = input("\nPaste the auth_code value here: ").strip()

    session.set_token(auth_code)
    resp = session.generate_token()
    token = resp.get("access_token")
    if not token:
        sys.exit(f"Login failed: {resp}")

    set_key(ENV_FILE, "FYERS_ACCESS_TOKEN", token)
    print("\nAccess token saved to .env. Now run the scan.")


class TokenError(SystemExit):
    """Access token missing or expired (stops the loop)."""


class HistoryClient:
    def __init__(self, adapter, stop=None):
        self.adapter, self.stop = adapter, stop

    def history(self, data):
        if self.stop is not None and self.stop.is_set():
            raise TokenError("MACD scan stopped with dashboard.")
        try:
            return self.adapter.call("history", data)
        except RuntimeError as exc:
            if any(word in str(exc) for word in ("authentication", "rate limit", "daily request budget", "Set FYERS")):
                raise TokenError(str(exc)) from None
            raise


def get_client():
    from collectors.fyers_data import Fyers
    config = json.loads((BASE / "config.json").read_text(encoding="utf-8-sig"))
    return HistoryClient(Fyers(BASE, config["requests_per_second"], config["requests_per_minute"], config["requests_per_day"]))


# ==========================================================
# DATA
# ==========================================================
def normalize_symbol(s):
    s = s.strip().upper()
    if not s or s.startswith("#"):
        return None
    if ":" not in s:
        s = f"NSE:{s}"
    # Fyers equity symbols need a series suffix: NSE:INFY -> NSE:INFY-EQ
    if not s.endswith(("-EQ", "-INDEX", "-BE", "-BZ", "-SM", "-ST", "-N1")):
        s += "-EQ"
    return s


def load_symbols(path):
    with open(path) as f:
        syms = [normalize_symbol(x) for x in f]
    return [s for s in dict.fromkeys(syms) if s]


def sheet_to_csv_url(url):
    """Turn a normal Google Sheets link into a CSV export link."""
    m = re.search(r"/spreadsheets/d/([\w-]+)", url)
    if not m:
        return url
    gid = re.search(r"[#&?]gid=(\d+)", url)
    out = f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
    return out + (f"&gid={gid.group(1)}" if gid else "")


def load_from_sheet(source, min_final, min_price=0):
    if source.lower().startswith("http"):
        g = re.search(r"[#&?]gid=(\d+)", source)
        print(f"Reading Google Sheet tab gid={g.group(1)}" if g
              else "Reading the FIRST tab of the Google Sheet (no #gid=... in the link)")
    src = sheet_to_csv_url(source) if source.lower().startswith("http") else source
    try:
        if src.lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(src, header=None)
        else:
            df = pd.read_csv(src, header=None, dtype=str)
    except Exception as e:
        sys.exit(f"Could not read the sheet: {e}\n"
                 "For a Google link, set sharing to 'Anyone with the link can view'. "
                 "Or download the sheet as CSV and pass that file path.")

    # Find the header row (titles / blank rows above the table are fine)
    want = {NAME_COL.lower()} | ({FINAL_COL.lower()} if min_final is not None else set())
    hdr = None
    for i in range(min(60, len(df))):
        if want <= {str(c).strip().lower() for c in df.iloc[i].tolist()}:
            hdr = i
            break
    if hdr is None:
        preview = [[str(c) for c in df.iloc[i].tolist() if str(c) != "nan"][:6] for i in range(min(4, len(df)))]
        sys.exit(f"No header row with '{NAME_COL}'" + (f" and '{FINAL_COL}'" if min_final is not None else "") + " was found in the first 60 rows.\n"
                 f"First rows seen: {preview}\n"
                 "A Google link without #gid=... reads the FIRST tab. Open the tab that has your table "
                 "and copy the link from the address bar (it ends with #gid=number).")

    heads = [str(c).strip().lower() for c in df.iloc[hdr].tolist()]
    name_i = heads.index(NAME_COL.lower())
    body = df.iloc[hdr + 1:]
    names = body.iloc[:, name_i].astype(str)
    valid = names.str.strip().ne("") & names.str.lower().ne("nan")

    if min_price and PRICE_COL.lower() in heads:
        pr = pd.to_numeric(body.iloc[:, heads.index(PRICE_COL.lower())].astype(str)
                           .str.replace(r"[^\d.\-]", "", regex=True), errors="coerce")
        before = int(valid.sum())
        valid = valid & (pr >= min_price).fillna(False)
        print(f"Price filter: kept {int(valid.sum())} of {before} with {PRICE_COL} >= {min_price:g}")

    if min_final is None:
        picked = names[valid]
        print(f"Sheet: {len(picked)} stocks, no {FINAL_COL} filter (add --final 4 to keep only {FINAL_COL} > 4%)")
    else:
        final_i = heads.index(FINAL_COL.lower())
        raw = body.iloc[:, final_i].astype(str)
        final = pd.to_numeric(raw.str.replace("%", "").str.replace(",", "").str.strip(), errors="coerce")
        # xlsx stores 17.47% as 0.1747; CSV export keeps the "17.47%" text
        if not raw.str.contains("%").any() and final.abs().max() <= 1:
            final = final * 100
        picked = names[valid & (final > min_final)]
        print(f"Sheet: {int(valid.sum())} stocks, {len(picked)} with {FINAL_COL} > {min_final:g}%")
        if len(picked) and final[valid].notna().any():
            print(f"       highest {FINAL_COL} in this tab: {final[valid].max():.2f}%")
    syms = [normalize_symbol(x) for x in picked]
    return [x for x in dict.fromkeys(syms) if x]


def fetch_candles(fyers, symbol, resolution, days):
    """Fetch candles in chunks, returns DataFrame indexed by IST datetime."""
    end = datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
    start = end - timedelta(days=days)
    step = timedelta(days=CHUNK_DAYS[resolution])

    rows = []
    cur = start
    while cur < end:
        nxt = min(cur + step, end)
        data = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": cur.strftime("%Y-%m-%d"),
            "range_to": nxt.strftime("%Y-%m-%d"),
            "cont_flag": "1",
        }
        for attempt in range(3):
            time.sleep(API_DELAY)
            resp = fyers.history(data=data)
            if resp.get("s") == "ok":
                rows += resp.get("candles", [])
                break
            if resp.get("code") == 429:       # rate limited
                time.sleep(2 * (attempt + 1))
                continue
            break                              # no data / bad symbol
        cur = nxt + timedelta(days=1)

    if not rows:
        return None

    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["dt"] = (
        pd.to_datetime(df["ts"], unit="s", utc=True)
        .dt.tz_convert("Asia/Kolkata")
        .dt.tz_localize(None)
    )
    df = df.drop_duplicates("ts").sort_values("dt").set_index("dt")
    return df


def resample_ohlc(daily, rule):
    out = daily.resample(rule, closed="right", label="right").agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["close"])


def month_rule():
    return "ME" if tuple(map(int, pd.__version__.split(".")[:2])) >= (2, 2) else "M"


# ==========================================================
# INDICATOR (same as Pine f_all -> MACD part)
# ==========================================================
def macd_state(df):
    if df is None or len(df) < SLOW + SIGNAL + 5:
        return None
    close = df["close"]
    m = close.ewm(span=FAST, adjust=False).mean() - close.ewm(span=SLOW, adjust=False).mean()
    sg = m.ewm(span=SIGNAL, adjust=False).mean()
    m_now, s_now, m_prev = m.iloc[-1], sg.iloc[-1], m.iloc[-2]

    pco = m_now >= s_now
    if pco and m_now > 0:
        trend = "Strong Bull"
    elif (not pco) and m_now < 0:
        trend = "Strong Bear"
    elif pco:
        trend = "Recovering"
    else:
        trend = "Weakening"

    return {
        "trend": trend,
        "price": round(float(close.iloc[-1]), 2),
        "macd": round(float(m_now), 4),
        "signal": round(float(s_now), 4),
        "rising": bool(m_now >= m_prev),
    }


# ==========================================================
# SCAN ONE STOCK  (top-down, lazy fetch)
# ==========================================================
def get_state(fyers, symbol, tf, cache):
    """Fetch only what is needed for this timeframe, then compute MACD state."""
    if tf in ("D", "W", "M"):
        if "D" not in cache:
            cache["D"] = fetch_candles(fyers, symbol, "D", LOOKBACK_DAYS["D"])
        daily = cache["D"]
        if daily is None:
            return None
        if tf == "D":
            df = daily
        elif tf == "W":
            df = resample_ohlc(daily, "W-FRI")
        else:
            df = resample_ohlc(daily, month_rule())
    else:
        df = fetch_candles(fyers, symbol, tf, LOOKBACK_DAYS[tf])
    return macd_state(df)


def scan_symbol(fyers, symbol, order, min_price=0):
    """
    order = timeframes highest -> lowest.
    Chain starts at order[0]; continues while lower TFs match the same side.
    Stops fetching at the first break (saves API calls).
    """
    cache = {}
    states = {}

    top = get_state(fyers, symbol, order[0], cache)
    states[order[0]] = top
    if OBSERVATION_CALLBACK and top and (not min_price or top['price'] > min_price):
        for tf in HTF_ORDER:
            if tf not in states:
                states[tf] = get_state(fyers, symbol, tf, cache)
        OBSERVATION_CALLBACK(symbol, {tf: states[tf]['trend'] if states[tf] else None for tf in HTF_ORDER}, top['price'])
    if not top or (top["trend"] not in ("Strong Bull", "Strong Bear") and not (order[0] == "M" and ((top["trend"] == "Recovering" and top["macd"] < 0) or (top["trend"] == "Weakening" and top["macd"] > 0)))):
        return None
    if min_price and top["price"] <= min_price:
        return None

    momentum = "weak" if top["trend"] in ("Recovering", "Weakening") else "strong"
    side = {"Recovering": "Strong Bull", "Weakening": "Strong Bear"}.get(top["trend"], top["trend"])
    chain = [order[0]]
    broke_at = None

    for tf in order[1:]:
        st = states[tf] if tf in states else get_state(fyers, symbol, tf, cache)
        states[tf] = st
        if st and st["trend"] == side:
            chain.append(tf)
        else:
            broke_at = tf
            break

    return {"momentum": momentum, "side": side, "chain": chain, "broke_at": broke_at, "states": states, "price": top["price"]}


# ==========================================================
# MAIN
# ==========================================================
# ==========================================================
# OUTPUT
# ==========================================================
CAT_FULL = "FULL"        # every timeframe aligned
CAT_NEAR = "NEAR"        # next lower TF already turning our way
CAT_PULL = "PULLBACK"    # next lower TF still against us


def categorize(side, broke_at, waiting_state):
    if broke_at is None:
        return CAT_FULL
    # Long: next TF "Recovering" = MACD above signal, below 0 (turning up)
    # Short: next TF "Weakening" = MACD below signal, above 0 (turning down)
    turning = "Recovering" if side == "BULL" else "Weakening"
    return CAT_NEAR if waiting_state == turning else CAT_PULL


def short_sym(sym):
    return sym.split(":")[-1].removesuffix("-EQ")


def print_block(rows, total_tfs, title, note):
    print(f"\n  {title}  ({len(rows)})")
    print(f"  {note}")
    if not rows:
        print("     -- none --")
        return
    print(f"  {'SYMBOL':<20}{'TFs':<6}{'CHAIN (high > low)':<36}{'WAITING ON'}")
    print("  " + "-" * 86)
    for x in rows:
        wait = "-" if x["not_yet_aligned"] == "-" else f"{x['not_yet_aligned']} ({x['waiting_state']})"
        print(f"  {short_sym(x['symbol']):<20}{str(x['count']) + '/' + str(total_tfs):<6}{x['chain']:<36}{wait}")


def print_report(bulls, bears, total_tfs, min_match, order):
    chain_txt = " > ".join(TF_LABEL[t] for t in order)
    stamp = datetime.now().strftime("%d-%b-%Y %H:%M")
    print("\n" + "#" * 90)
    print(f"  MACD TOP-DOWN SCAN | {stamp} | {chain_txt} | min chain {min_match}")
    print("#" * 90)

    for head, rows, side in (
        ("\U0001F7E2 ACTIVE BUY SETUPS (Long Entry)", bulls, "BULL"),
        ("\U0001F534 ACTIVE SELL / EXIT SIGNALS", bears, "BEAR"),
    ):
        print("\n" + "=" * 90)
        print(head + f"   [{len(rows)} stocks]")
        print("=" * 90)
        full = [x for x in rows if x["category"] == CAT_FULL]
        near = [x for x in rows if x["category"] == CAT_NEAR]
        pull = [x for x in rows if x["category"] == CAT_PULL]
        if side == "BULL":
            print_block(full, total_tfs, "\u2B50 FULL ALIGNMENT", "All timeframes Strong Bull - trend running, look for breakout / continuation entry")
            print_block(near, total_tfs, "\U0001F3AF ENTRY ZONE", "Higher TFs Strong Bull, next lower TF already turning up (Recovering) - trigger close")
            print_block(pull, total_tfs, "\U0001F551 PULLBACK WATCHLIST", "Higher TFs Strong Bull, lower TF still pulling back - wait for it to turn")
        else:
            print_block(full, total_tfs, "\u2B50 FULL ALIGNMENT", "All timeframes Strong Bear - downtrend running, short / exit longs")
            print_block(near, total_tfs, "\U0001F3AF ENTRY ZONE", "Higher TFs Strong Bear, next lower TF turning down (Weakening) - trigger close")
            print_block(pull, total_tfs, "\U0001F551 BOUNCE WATCHLIST", "Higher TFs Strong Bear, lower TF bouncing - wait for it to roll over")
    print()


# ==========================================================
# HTML REPORT
# ==========================================================
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
__REFRESH__
<title>MACD top-down scan</title>
<style>
:root{--bg:#f5f6f8;--panel:#fff;--ink:#161b22;--mute:#667085;--line:#e4e7ec;
--bull:#0a8f6c;--bull-bg:#e3f5ef;--bear:#d0364a;--bear-bg:#fde8eb;--wait:#a86b0a;--wait-bg:#fdf3dc}
@media(prefers-color-scheme:dark){:root{--bg:#0f1318;--panel:#171c23;--ink:#e8ebf0;--mute:#8a94a3;--line:#262d37;
--bull:#3ccf9f;--bull-bg:#12342b;--bear:#ff6b7d;--bear-bg:#3a1a20;--wait:#f0b84a;--wait-bg:#3a2e12}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
body[data-side=buy]{--acc:var(--bull);--acc-bg:var(--bull-bg)}
body[data-side=sell]{--acc:var(--bear);--acc-bg:var(--bear-bg)}
main{max-width:980px;margin:0 auto;padding:28px 18px 60px}
h1{font-size:22px;margin:0 0 4px;font-weight:650}
.meta{color:var(--mute);font-size:13px;margin:0 0 22px}
.bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:18px}
.tab{appearance:none;border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:9px 16px;border-radius:8px;
font:inherit;font-weight:600;cursor:pointer;display:flex;gap:8px;align-items:center}
.tab b{background:var(--line);border-radius:10px;padding:0 8px;font-size:12px;line-height:20px}
.tab[aria-selected=true]{border-color:var(--acc);box-shadow:inset 0 -3px 0 var(--acc)}
.tab[aria-selected=true] b{background:var(--acc-bg);color:var(--acc)}
.sp{flex:1}
input[type=search]{border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:9px 12px;border-radius:8px;font:inherit;width:170px}
.btn{border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:9px 14px;border-radius:8px;font:inherit;cursor:pointer}
:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin-bottom:16px;overflow:hidden}
section h2{margin:0;padding:14px 16px 2px;font-size:16px;font-weight:650;display:flex;gap:8px;align-items:center}
section h2 span{color:var(--mute);font-weight:500;font-size:13px}
section p{margin:0;padding:0 16px 10px;color:var(--mute);font-size:13px}
.row{display:grid;grid-template-columns:110px 1fr 190px;gap:12px;align-items:center;padding:10px 16px;border-top:1px solid var(--line)}
.sym{font-weight:650;color:var(--ink);text-decoration:none}
.sym:hover{color:var(--acc);text-decoration:underline}
.chips{display:flex;gap:5px;flex-wrap:wrap}
.chip{min-width:34px;text-align:center;font-size:12px;font-weight:600;padding:3px 7px;border-radius:6px;border:1px solid var(--line);color:var(--mute)}
.chip.on{background:var(--acc-bg);border-color:var(--acc);color:var(--acc)}
.chip.next{background:var(--wait-bg);border:1px dashed var(--wait);color:var(--wait)}
.wait{font-size:13px;color:var(--mute)}
.wait b{color:var(--wait);font-weight:600}
.empty{padding:18px 16px;color:var(--mute);border-top:1px solid var(--line);font-size:14px}
.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--mute);font-size:12px;margin:0 0 18px}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:var(--ink);color:var(--bg);padding:8px 14px;border-radius:8px;font-size:13px;opacity:0;pointer-events:none;transition:opacity .2s}
.toast.show{opacity:1}
main{max-width:1040px}
.bar{position:sticky;top:0;z-index:5;background:var(--bg);padding:10px 0;margin:0 0 8px}
select{border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:9px 10px;border-radius:8px;font:inherit}
summary{list-style:none;cursor:pointer;display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center;padding:14px 16px}
summary::-webkit-details-marker{display:none}
summary::before{content:"";width:7px;height:7px;border-right:2px solid var(--mute);border-bottom:2px solid var(--mute);transform:rotate(-45deg);transition:transform .15s}
details[open]>summary::before{transform:rotate(45deg)}
.gt{font-weight:650;font-size:16px}
.gc{background:var(--acc-bg);color:var(--acc);border-radius:10px;padding:0 8px;font-size:12px;font-weight:600;line-height:20px}
.gn{color:var(--mute);font-size:13px;flex-basis:100%;padding-left:17px}
.gempty{display:flex;gap:10px;align-items:center;padding:10px 16px;margin-bottom:16px;border:1px dashed var(--line);border-radius:10px;color:var(--mute);font-size:14px}
.gempty .gt{font-size:14px;font-weight:600;color:var(--mute)}
.row{grid-template-columns:110px 84px 1fr 48px 210px}
.price{font-variant-numeric:tabular-nums;font-weight:600;text-align:right}
input[type=number]{border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:9px 12px;border-radius:8px;font:inherit;width:120px}
.row:hover{background:color-mix(in srgb,var(--acc) 7%,transparent)}
.cnt{font-size:13px;font-weight:600;color:var(--acc);text-align:right}
.st{color:var(--mute)}
@media(max-width:640px){.row{grid-template-columns:1fr;gap:6px}.cnt{text-align:left}input[type=search],input[type=number]{width:100%}.price{text-align:left}}
</style>
</head>
<body data-side="buy">
<main>
  <h1>MACD top-down scan</h1>
  <p class="meta" id="meta"></p>
  <div class="bar">
    <button class="tab" role="tab" id="t-buy" aria-selected="true">Buy setups <b id="n-buy">0</b></button>
    <button class="tab" role="tab" id="t-sell" aria-selected="false">Sell / exit signals <b id="n-sell">0</b></button>
    <span class="sp"></span>
    <select id="sort" aria-label="Sort"><option value="len">Longest chain first</option><option value="phigh">Price: high to low</option><option value="plow">Price: low to high</option><option value="az">A to Z</option></select>
    <input type="number" id="minp" placeholder="Min price" min="0" aria-label="Minimum price">
    <input type="search" id="q" placeholder="Filter symbol" aria-label="Filter by symbol">
    <button class="btn" id="copy">Copy for TradingView</button>
  </div>
  <div class="legend">
    <span><span class="chip on">D</span> aligned</span>
    <span><span class="chip next">4H</span> next timeframe to confirm</span>
    <span><span class="chip">1H</span> not checked yet</span>
  </div>
  <div id="out"></div>
</main>
<div class="toast" id="toast"></div>
<script>
let D = __DATA__;
const state = {side: "buy", q: "", sort: "len", minp: 0, live: null};
const GROUPS = {
  buy: [
    ["FULL", "Full alignment", "Every timeframe is Strong Bull. Trend is running; look for a continuation or breakout entry."],
    ["NEAR", "Entry zone", "Higher timeframes are Strong Bull and the next one down is already turning up. Trigger is close."],
    ["PULLBACK", "Pullback watchlist", "Higher timeframes are Strong Bull but a lower one is still pulling back. Wait for it to turn."]],
  sell: [
    ["FULL", "Full alignment", "Every timeframe is Strong Bear. Downtrend is running; short or exit longs."],
    ["NEAR", "Entry zone", "Higher timeframes are Strong Bear and the next one down is turning down. Trigger is close."],
    ["PULLBACK", "Bounce watchlist", "Higher timeframes are Strong Bear but a lower one is bouncing. Wait for it to roll over."]]
};
const esc = t => String(t).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

const fmtP = p => p == null ? "-" : Number(p).toLocaleString("en-IN", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const PHRASE = {"Recovering": "turning up", "Weakening": "turning down", "Strong Bull": "still bullish", "Strong Bear": "still bearish"};

function rowHtml(r) {
  const chips = D.order.map(tf =>
    `<span class="chip ${r.aligned.includes(tf) ? "on" : tf === r.waiting ? "next" : ""}">${esc(tf)}</span>`).join("");
  const wait = r.waiting === "-" ? "All timeframes aligned"
    : `Waiting on <b>${esc(r.waiting)}</b> <span class="st">${esc(PHRASE[r.waiting_state] || r.waiting_state)}</span>`;
  return `<div class="row">
    <a class="sym" target="_blank" rel="noopener" href="https://www.tradingview.com/chart/?symbol=${encodeURIComponent(r.tv)}">${esc(r.short)}</a>
    <div class="price">${fmtP(r.price)}</div>
    <div class="chips">${chips}</div><div class="cnt" title="Timeframes aligned">${r.count}/${D.order.length}</div><div class="wait">${wait}</div></div>`;
}

function visible(side) {
  const q = state.q.trim().toUpperCase();
  const rows = D[side].filter(r => (!q || r.short.includes(q)) && (!state.minp || (r.price || 0) >= state.minp));
  const byName = (a, b) => a.short.localeCompare(b.short);
  const byLen = (a, b) => (b.count - a.count) || byName(a, b);
  const sorters = {az: byName, len: byLen,
    phigh: (a, b) => ((b.price || 0) - (a.price || 0)) || byLen(a, b),
    plow: (a, b) => ((a.price || 0) - (b.price || 0)) || byLen(a, b)};
  return rows.sort(sorters[state.sort] || byLen);
}

function setMeta() {
  document.getElementById("meta").textContent =
    `${D.stamp}  |  ${D.order.join(" > ")}  |  minimum chain ${D.min}  |  ${D.scanned} stocks scanned` +
    (state.live === true ? "  |  live" : state.live === false ? "  |  scanner stopped" : "");
}

function render() {
  setMeta();
  document.body.dataset.side = state.side;
  for (const s of ["buy", "sell"]) {
    document.getElementById("t-" + s).setAttribute("aria-selected", s === state.side);
    document.getElementById("n-" + s).textContent = D[s].length;
  }
  const rows = visible(state.side);
  let html = "";
  for (const [key, title, note] of GROUPS[state.side]) {
    const g = rows.filter(r => r.category === key);
    html += g.length
      ? `<section><details open><summary><span class="gt">${title}</span><span class="gc">${g.length}</span><span class="gn">${note}</span></summary>${g.map(rowHtml).join("")}</details></section>`
      : `<div class="gempty"><span class="gt">${title}</span><span>None right now</span></div>`;
  }
  if (D.pending)
    html = `<section><div class="empty">First scan is running. Results appear here by themselves.</div></section>`;
  else if (!D[state.side].length)
    html = `<section><div class="empty">No stocks matched. Try a lower minimum chain (--min 2) or start the chain lower (--anchor W).</div></section>`;
  document.getElementById("out").innerHTML = html;
}

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 1800);
}

document.getElementById("t-buy").onclick = () => { state.side = "buy"; render(); };
document.getElementById("t-sell").onclick = () => { state.side = "sell"; render(); };
document.getElementById("minp").oninput = e => { state.minp = parseFloat(e.target.value) || 0; render(); };
document.getElementById("sort").onchange = e => { state.sort = e.target.value; render(); };
document.getElementById("q").oninput = e => { state.q = e.target.value; render(); };
document.getElementById("copy").onclick = async () => {
  const list = visible(state.side).map(r => r.tv).join(",");
  if (!list) return toast("Nothing to copy");
  try { await navigator.clipboard.writeText(list); toast("Copied " + visible(state.side).length + " symbols"); }
  catch (e) { prompt("Copy these symbols:", list); }
};
if (location.protocol.indexOf("http") === 0) {
  let ver = D.version || 0;
  const poll = async () => {
    try {
      const d = await (await fetch("/data.json", {cache: "no-store"})).json();
      state.live = true;
      if (d.version && d.version !== ver) { ver = d.version; D = d; render(); toast("Updated " + d.stamp); }
      else setMeta();
    } catch (e) { state.live = false; setMeta(); }
  };
  setInterval(poll, 5000);
}
render();
</script>
</body>
</html>
"""


LAST_DATA = None


def render_html(data):
    payload = json.dumps(data).replace("</", "<\\/")
    return HTML_TEMPLATE.replace("__REFRESH__", "").replace("__DATA__", payload)


def write_html(bulls, bears, order, min_match, scanned, refresh_secs=0, errors=None):
    def conv(x):
        return {
            "short": short_sym(x["symbol"]),
            "tv": x["symbol"].replace("-EQ", ""),
            "count": x["count"],
            "aligned": x["chain"].split(" > "),
            "waiting": x["not_yet_aligned"],
            "waiting_state": x["waiting_state"],
            "category": x["category"],
            "price": x.get("price"),
            "momentum": x.get("momentum", "strong"),
            "states": x.get("states", {}),
        }
    data = {
        "stamp": datetime.now().strftime("%d %b %Y, %H:%M"),
        "order": [TF_LABEL[t] for t in order],
        "min": min_match,
        "scanned": scanned,
        "errors": errors or [],
        "momentum_version": 1,
        "buy": [conv(x) for x in bulls],
        "sell": [conv(x) for x in bears],
    }
    data.update(REPORT_METADATA)
    global LAST_DATA
    LAST_DATA = data
    temporary = Path(OUTPUT_HTML).with_suffix(".tmp")
    temporary.write_text(render_html(data), encoding="utf-8")
    temporary.replace(OUTPUT_HTML)
    return os.path.abspath(OUTPUT_HTML)


def run(min_match, anchor, source=None, min_final=FINAL_MIN, open_browser=OPEN_BROWSER, refresh_secs=0, min_price=MIN_PRICE, symbols_override=None):
    if anchor not in HTF_ORDER:
        sys.exit(f"--anchor must be one of {HTF_ORDER}")
    order = HTF_ORDER[HTF_ORDER.index(anchor):]

    fyers = get_client()
    symbols = list(dict.fromkeys(symbols_override)) if symbols_override is not None else (load_from_sheet(source, min_final, min_price) if source else load_symbols(STOCKS_FILE))
    if not symbols:
        sys.exit("No symbols to scan.")
    print(f"Scanning {len(symbols)} stocks | top-down: "
          f"{' > '.join(TF_LABEL[t] for t in order)} | chain >= {min_match}\n")

    bulls, bears, errors = [], [], []

    for i, sym in enumerate(symbols, 1):
        print(f"\r  Progress: {i}/{len(symbols)}", end="", flush=True)
        if PROGRESS_CALLBACK:
            PROGRESS_CALLBACK(f"{i}/{len(symbols)} · {sym}")
        try:
            r = scan_symbol(fyers, sym, order, min_price)
        except Exception as e:
            errors.append(f"{sym}: {e}")
            continue

        if not r or len(r["chain"]) < min_match:
            continue

        side = "BULL" if r["side"] == "Strong Bull" else "BEAR"
        row = {
            "symbol": sym,
            "momentum": r["momentum"],
            "states": {TF_LABEL[t]: st for t, st in r["states"].items()},
            "price": r["price"],
            "side": side,
            "count": len(r["chain"]),
            "chain": " > ".join(TF_LABEL[t] for t in r["chain"]),
            "not_yet_aligned": TF_LABEL[r["broke_at"]] if r["broke_at"] else "-",
            "waiting_state": (r["states"][r["broke_at"]]["trend"]
                              if r["broke_at"] and r["states"].get(r["broke_at"]) else "-"),
        }
        row["category"] = categorize(side, r["broke_at"], row["waiting_state"])
        for t in order:
            st = r["states"].get(t)
            row[TF_LABEL[t]] = st["trend"] if st else "-"

        (bulls if side == "BULL" else bears).append(row)

    bulls.sort(key=lambda x: -x["count"])
    bears.sort(key=lambda x: -x["count"])

    print()  # end progress line
    print_report(bulls, bears, len(order), min_match, order)
    if errors:
        print(f"  {len(errors)} symbol(s) skipped due to errors (e.g. {errors[0]})\n")

    if len(errors) == len(symbols):
        raise RuntimeError("All MACD symbols failed; previous report preserved. " + errors[0])
    html_path = write_html(bulls, bears, order, min_match, len(symbols), refresh_secs, errors)
    print(f"  HTML report: {html_path}")
    if open_browser:
        webbrowser.open("file://" + html_path)

    out = pd.DataFrame(bulls + bears)
    if not out.empty:
        out.to_csv(OUTPUT_CSV, index=False)
        print(f"\nSaved: {OUTPUT_CSV}")
    else:
        print("\nNo stocks matched.")


# ==========================================================
# SCHEDULED LOOP (--every 15 / --every 60)
# ==========================================================
IST = timezone(timedelta(hours=5, minutes=30))


def ist_now():
    return datetime.now(IST)


def market_open(now):
    t = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9 * 60 + 15 <= t <= 15 * 60 + 35


def next_slot(now, every):
    """Next clock mark (e.g. :00 :15 :30 :45 for 15) plus SLOT_DELAY seconds."""
    mins = now.hour * 60 + now.minute
    nxt = (mins // every + 1) * every
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(minutes=nxt, seconds=SLOT_DELAY)


LIVE = {"data": None, "seen": 0.0}


class DashHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/data.json":
            body, ctype = json.dumps(LIVE["data"] or {}).encode(), "application/json"
        elif path in ("/", "/index.html"):
            body, ctype = render_html(LIVE["data"]).encode("utf-8"), "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        LIVE["seen"] = time.time()      # an open page checks in every few seconds
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def start_server():
    for port in range(DASH_PORT, DASH_PORT + 10):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), DashHandler)
        except OSError:
            continue
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{port}/"
    return None


def pending_data(args):
    anchor = args.anchor if args.anchor in HTF_ORDER else ANCHOR
    order = HTF_ORDER[HTF_ORDER.index(anchor):]
    return {"pending": True, "stamp": "waiting for first scan", "order": [TF_LABEL[t] for t in order],
            "min": args.min, "scanned": 0, "buy": [], "sell": [], "version": 0}


def publish_and_open(url, first):
    """Push the finished scan to the dashboard; open a tab only if none is open."""
    if LAST_DATA is None:
        return
    data = dict(LAST_DATA)
    data["version"] = int(time.time() * 1000)
    LIVE["data"] = data
    if not url:                                   # no local server: fall back to the file
        if first and OPEN_BROWSER:
            webbrowser.open("file://" + os.path.abspath(OUTPUT_HTML))
        return
    if time.time() - LIVE["seen"] > OPEN_IF_IDLE_SECS:
        if OPEN_BROWSER:
            webbrowser.open(url)
            print("  Dashboard opened in your browser")
    else:
        print("  Dashboard updated in your open tab")


def loop(every, args, always):
    global LAST_DATA
    LIVE["data"] = pending_data(args)
    url = start_server()
    print(f"Auto-scan every {every} min (Ctrl+C to stop).")
    if url:
        print(f"Live dashboard: {url}  (bookmark it; it updates itself after every scan)")
    first = True
    try:
        while True:
            now = ist_now()
            if first or always or not MARKET_ONLY or market_open(now):
                print(f"\n[{now:%d-%b %H:%M:%S} IST] scanning...")
                LAST_DATA = None
                try:
                    run(args.min, args.anchor, args.source, args.final, open_browser=False, min_price=args.min_price)
                except TokenError as e:
                    print(f"\nStopped: {e}")
                    return
                except SystemExit as e:
                    print(f"\nScan skipped: {e}")
                except Exception as e:
                    print(f"\nScan failed: {e}")
                else:
                    publish_and_open(url, first)
                first = False
            else:
                print(f"\n[{now:%H:%M}] market closed, waiting...")
            target = next_slot(ist_now(), every)
            print(f"  Next scan at {target:%a %H:%M:%S} IST")
            time.sleep(max(1, (target - ist_now()).total_seconds()))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="generate Fyers access token")
    ap.add_argument("--min", type=int, default=MIN_TF_MATCH, help="min chain length")
    ap.add_argument("--anchor", default=ANCHOR, help="highest TF to start chain: M W D 240 60 15 5")
    ap.add_argument("--source", default=SHEET_SOURCE, help="Google Sheet URL or .csv/.xlsx path")
    ap.add_argument("--final", type=float, default=FINAL_MIN, help="keep rows where Final > this percent")
    ap.add_argument("--min-price", type=float, default=MIN_PRICE, help="skip stocks priced below this")
    ap.add_argument("--every", type=int, default=0, help="repeat every N minutes (15 or 60 recommended)")
    ap.add_argument("--always", action="store_true", help="with --every: also scan outside market hours")
    args = ap.parse_args()

    if args.login:
        login()
    elif args.every:
        loop(args.every, args, args.always)
    else:
        try:
            run(args.min, args.anchor, args.source, args.final, min_price=args.min_price)
        except TokenError as e:
            sys.exit(str(e))
