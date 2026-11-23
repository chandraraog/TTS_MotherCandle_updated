# Chandra Market Desk — Money, OI and MACD

This folder is independent of `../OIData`. It neither imports nor modifies the reference files.

## Start

From `C:\dashboard\TTS_MotherCandle_updated` in PowerShell:

```powershell
py -m pip install -r OIDataV2/requirements.txt`r`n# Includes Astro Timing and Swiss Ephemeris dependencies for the shared server
py OIDataV2/server.py
or
C:\dashboard\TTS_MotherCandle_updated> python .\OIDataV2\server.py
```

Open:

- Money: http://127.0.0.1:8765/money.html
- OI: http://127.0.0.1:8765/oi.html

Or double-click `start_live.cmd`. Keep its terminal running; Ctrl+C stops the server.
Do not open the HTML files directly with `file://`: they read snapshots from the Python server.
No cloud deployment or external web assets are required. No order-placement endpoints are used.

## Secure access from another device (local computer or any VPS)

The default remains localhost-only. For remote access, use a trusted HTTPS reverse proxy or VPN/tunnel in front of this server. Do not expose plain HTTP or port 8765 directly to the public internet.

Set a strong dashboard login in the server process environment (the browser will show a login prompt):

```powershell
$env:DASHBOARD_USERNAME = 'dashboard-user'
$env:DASHBOARD_PASSWORD = 'use-a-long-unique-password-here'
```

For a VPS or a local computer behind an HTTPS reverse proxy, keep Python bound to localhost and allow only the external hostname and origin used by that proxy:

```powershell
py OIDataV2/server.py --allowed-host dashboard.example.com --allowed-origin https://dashboard.example.com
```

Configure the proxy to forward HTTPS traffic for `dashboard.example.com` to `http://127.0.0.1:8765`, preserve the Host header, and set `X-Forwarded-Proto: https`. On a VPS, allow inbound HTTPS (443) in the provider firewall/security group; keep port 8765 private. A local computer needs a VPN or secure tunnel for access away from its Wi-Fi. When using a different hostname or port, set the exact `--allowed-host` value (including a nonstandard port) and exact browser `--allowed-origin` URL. Repeat either option for additional names/origins.

Direct HTTPS binding is also available when you already have a certificate and private key:

```powershell
py OIDataV2/server.py --host 0.0.0.0 --port 8765 --allowed-host dashboard.example.com:8765 --allowed-origin https://dashboard.example.com:8765 --tls-cert C:\certs\fullchain.pem --tls-key C:\certs\privkey.pem
```

Network binding refuses to start without both dashboard login variables, an allowed host, an HTTPS origin, and TLS certificate/key files. The server rejects remote requests unless they arrive over HTTPS. This keeps the app portable across Windows, AWS, and other VPS providers; only the HTTPS proxy, DNS, and firewall setup differ. Do not put the password in the command line or commit it to source control.

For a preview without any credentials or network requests:

```powershell
py OIDataV2/server.py --demo
```

Or double-click `start_demo.cmd`. Demo uses **data/demo.sqlite** and **config.demo.json**;
live uses **data/market_data.sqlite** and **config.json**. Synthetic rows are visibly labelled.
Use `--port 8766` if another app is using port 8765. Run only one live collector instance.

## Fyers credentials

The server reads the existing project `.env`, or a separate `OIDataV2/.env`:

```dotenv
FYERS_CLIENT_ID=your_app_id
FYERS_ACCESS_TOKEN=your_current_access_token
```

The V2 `.env` overrides the parent file; process environment variables override both.
Credentials stay on the Python side and are not sent to the HTML page, logged, or stored in SQLite.
Renew the access token using your existing login workflow. A changed `.env` token is picked up on the next request;
there is no automatic login or modification of your existing token manager.

## Defaults and controls

| Job | Default | Controls |
| --- | --- | --- |
| Money | NIFTY 50, nearest expiry, ATM ±5 strikes, 60 seconds | Money page |
| Fyers OI | All NSE F&O underlyings, 3600 seconds | OI page; supports custom watchlist and daily mode |
| NSE history | Last 5 available trading days, download after 18:30 IST, retry every 30 minutes | OI page, expanded settings |
| Market hours | Weekdays 09:15–15:30 IST | `config.json` |
| Retention | 40 days by default; configurable in Open Interest settings | `config.json` |
| Shared Fyers rate | 0.5 requests/second; 40/minute; 4500/day V2 budget | `config.json` |

**Save settings** persists settings. Automatic collection follows the schedule.
**Collect now** runs the selected source immediately, including outside market hours or when NSE automatic download is disabled.
The Source selector on the OI page determines whether Collect now runs Fyers or NSE.
Selecting a historical snapshot reads SQLite and does not fetch the broker again.
CSV export exports the currently displayed/filtered rows.

Money is a single selected instrument at a time; any supported NSE underlying can be typed into the selector.
Choose Nearest expiry when changing underlying. Choose a specific expiry after its first snapshot loads.
"All" means all current NSE futures underlyings in the Fyers instrument master, not cash-market stocks without F&O.
The custom watchlist uses exact Fyers symbols, e.g. `NSE:SBIN-EQ` and `NSE:NIFTY50-INDEX`.

Schedules are independent worker threads sharing a throttled Fyers connection gate. Intervals are measured
from the end of a collection to the start of its next run, so a slow all-stock scan never overlaps itself.
One all-stock run takes several minutes. Multiple browser tabs read the same snapshots; they do not multiply API requests.
Fyers Standard currently allows 50 data requests/minute and 5000/day. The conservative V2 defaults
reserve headroom at 40/minute and 4500/day, with a 0.5/second pace. Counts persist across restarts;
a broker rate rejection activates a five-minute shared cooldown. A daily-budget stop makes no further
market-data calls until the next IST day. Other programs using the same Fyers account consume the
same upstream allowance and are not counted by V2.

The requested 60-second Money setting plus hourly all-stock OI is **not guaranteed to fit Standard's
daily allowance**: each ±5-strike Money run takes approximately 23 requests, and each broad scan
requires approximately two requests per underlying. For a full-day Standard workflow, start with
Money every 180 seconds and OI once daily, then adjust based on the displayed request counter.
For an account with verified higher limits, raise `requests_per_second`, `requests_per_minute` and
`requests_per_day` in config.json. Never assume an upgraded plan automatically.
Reference: https://support.fyers.in/portal/en/kb/articles/what-are-the-fyers-api-rate-limits-for-standard-and-prime-plans

Configure exchange holidays in `market.holidays` as `YYYY-MM-DD` strings. Holiday discovery is not automatic.
Special-session hours need a configuration change or manual collection. Daily time fields are always IST.
The scheduler runs only while this Python process is running. It is not installed as a Windows service or startup task.
Direct edits to config.json require a restart; page settings apply immediately.

## Calculations and data quality

NSE daily OI rows include the two nearest unexpired futures and options expiries. The Open Interest page's Current/Next selector ranks those separately: index futures remain monthly, while NIFTY options use the nearest two listed weekly expiries. The table's term filter matches a term found in Future, CE, or PE. Signal columns combine the term with the side-specific view, shortening “Aggressive” to “Agg.” Old saved NSE snapshots are refreshed from NSE the next time Collect now is used.

Recent OI summaries also show month-to-date changes for the monthly future and the CE/PE option chain expiring with that future. Monthly CE/PE changes sum every strike for the monthly expiry; they are distinct from the selected ATM option changes. Existing snapshots can contribute when their saved current/next option expiry matches the monthly future expiry. Collect now refreshes older NSE cache payloads with the monthly fields.

```
Money (₹ lakh) = (OI − previous-day OI) × ATP × oi_multiplier / 100000
```

- Fyers Market Depth supplies `oi`, `pdoi`, and `atp` for each option contract.
- Default `oi_multiplier = 1` assumes OI is reported in units. Compare a matching live contract against Excel.
  If your source reports lots, use the applicable lot size instead; do not multiply an already unit-based OI twice.
- Missing fields or nonpositive ATP produce an unavailable Money value, not zero or an LTP substitution.
- Negative Money is retained as unwinding. The aggregate writing-bias heuristic is not actual measured cash flow
  and cannot identify buyers versus sellers. It uses 20% minimum absolute difference and suppresses directional
  writing labels when either side's total is negative.
- CE/PE Money totals cover the selected strike range only. An incomplete side has an unavailable total.
- Snapshot deltas compare identical option/future contracts within the same IST date. They reset across a date
  boundary, contract rollover, or instrument/expiry selection change. Money delta is the difference between two
  Money readings; it is not `(OI_now - OI_last) * ATP_now`.
- OI classifications use price change and OI change versus the previous trading day. Futures, CE and PE
  classifications follow the supplied reference report. Zero/missing changes are unclassified.
- Fyers OI uses the nearest futures contract and nearest option expiry. ATM is nearest the futures price among
  the returned chain's ±50 spot strikes; futures and options expiries are displayed separately.
- Fyers total CE/PE OI and PCR use provider aggregate fields. NSE totals are summed across all strikes for the
  nearest option expiry. These sources can differ in timing and coverage and are labelled separately.
- Collections are sequential observations, not an atomic exchange snapshot. Each Money row stores observation
  and last-trade times; OI rows store observation times. The collection timestamp is **receipt time**, not proof
  that exchange OI changed at that instant. Last-trade time describes trades, not an OI update timestamp.
- Missing instruments appear under Collection status. A total failure retains the last successful snapshot and
  displays an error. Partial snapshots explicitly report failed/incomplete rows.
- NSE daily data comes from published bhavcopies. Re-running it hourly does not produce hourly observations.
  Unpublished days (404) are skipped, and the displayed trading date remains explicit. Other download errors
  are surfaced rather than silently substituted with demo data.

## One-off collection / external scheduling

These commands collect into the live database and exit without a web server:

```powershell
py OIDataV2/server.py --collect-once money
py OIDataV2/server.py --collect-once oi
py OIDataV2/server.py --collect-once nse
py OIDataV2/server.py --collect-once all
```

Exit code is nonzero if a requested job fails. One-off commands deliberately ignore market-hour gating.
Do not schedule one-off commands while another live collector is running against the same database.
Start the server normally afterward to browse the stored data.

## Files

- `server.py`: localhost routes by default, optional authenticated HTTPS remote access, static pages, CLI.
- `engine.py`: scheduler, configuration validation, demo mode, snapshot deltas.
- `calculations.py`: Money and interpretation functions.
- `collectors/fyers_data.py`: public instrument master, read-only Fyers data requests.
- `collectors/nse_daily.py`: cached NSE bhavcopy history and analysis.
- `storage.py`: SQLite snapshot storage and retention.
- `web/`: two HTML views plus shared CSS and JavaScript.
- `data/`: generated SQLite databases and cached public market data, ignored by Git.

## Verification

```powershell
python -m unittest discover -s OIDataV2 -p test_dashboard.py -v
python -m compileall -q OIDataV2
node --check OIDataV2/web/app.js
```

Node is only used for the optional JavaScript syntax check, not to run the dashboard.
Tests use isolated temporary directories under `OIDataV2/data/tests` and mocked upstream data; they do not trade
or contact Fyers. The HTTP integration test needs localhost socket access.

API references:
- https://fyers.in/api-docs/v3/api/market-data/market-depth/
- https://fyers.in/api-docs/v3/api/market-data/option-chain/
- https://fyers.in/api-docs/v3/api/market-information/symbol-master/symbol-master-file/

## Compact interface and MACD tab

The three tabs are Money, Open interest, and MACD Scanner. Black/White theme choice is saved
in this browser and shared across the tabs. Collection settings are collapsed by default;
Essential/All columns controls table density (CSV export still includes all fields).

When the first OI view has no Fyers snapshot, the page shows saved NSE daily data with an explicit
source notice. Selecting Fyers manually keeps that source selected. Running-job progress is visible
above the table, including while browsing another saved source.

MACD Scanner uses `OIDataV2/macd_mtf_scanner.py`, which was moved from the project root.
The old root command remains a compatibility launcher. New HTML/CSV reports are saved in OIDataV2;
stocks.txt and the existing .env are resolved from the parent project, independent of the working directory.

Server startup does not launch a MACD scan. `--no-macd` remains accepted for compatibility. Demo mode never starts MACD broker requests. The MACD tab has **Run scan**,
**Reload report**, progress, and errors; only one scan runs at a time. Previous results remain
available until a new report is completely written. Startup scans and UI scans share the Money/OI
Fyers rate limiter and persistent daily request budget. Six years of daily candles plus lower-timeframe
checks can take several minutes for a full watchlist. A rate/authentication failure stops the scan
and preserves the previous report rather than publishing an empty success.

The server does not create a second MACD web server or open additional browser windows. The standalone
scanner's optional repeat-mode web server now uses port 8770 to avoid colliding with this dashboard.

Launch everything:

```powershell
python C:\dashboard\TTS_MotherCandle_updated\OIDataV2\server.py
```

Manual standalone scan (optional):

```powershell
python C:\dashboard\TTS_MotherCandle_updated\OIDataV2\macd_mtf_scanner.py
```

## MACD stock-list selection

Choose **Watchlist (stocks.txt)** or **Bhavcopy F&O** beside Run scan. This chooses instruments,
not the candle provider: both modes calculate all configured MACD timeframes from Fyers candles.
Bhavcopy F&O uses the latest saved NSE daily OI report, maps its underlying names through the
current Fyers instrument master, and displays the bhavcopy trading date. It is the F&O universe,
not every cash-market stock. Collect NSE daily data first if none exists.

Switching the selector only loads that source's saved report. Click Run scan to calculate it.
Watchlist and bhavcopy reports are kept separately as macd_scan_report.html and
macd_bhavcopy_report.html (and matching CSV files). The last source actually started is saved in
data/macd_settings.json and used for subsequent manual or enabled scheduled scans. The UI remembers its
view selection separately. Only one MACD scan runs at a time. A full bhavcopy list takes longer
and consumes more Fyers requests than a small watchlist, using the same shared request budget.

### Hourly watchlist and Telegram
The live server schedules watchlist scans every 60 minutes during configured IST market hours (weekends and configured holidays excluded), with no overlapping MACD jobs. Keep the server running. Manual watchlist scans reset the hourly timer. Toggle the schedule and Telegram independently with Save schedule / alerts. Telegram defaults off; credentials are TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the parent or V2 .env.

Watchlist observations independently evaluate M/W/D/1H using the scanner Strong Bull / Strong Bear definitions. A first observation establishes a baseline; an alert requires exactly two aligned timeframes in the previous observation to remain aligned and grow to at least three in the same direction. Incomplete observations are ignored. Price cutoff applies before observation. Bhavcopy scans do not send these watchlist alerts. SQLite persists observations and one alert attempt per symbol per IST day across restarts. Delivery failures/timeouts are displayed and not retried that day to prevent duplicates. Alerts use latest available candles, which may be unfinished.

OI collection is now manual only for both Fyers and NSE bhavcopy, overriding earlier schedule descriptions. Startup, page refreshes, and settings changes do not collect OI. The OI page defaults to saved NSE bhavcopy; Collect now refreshes the selected source. Money and MACD schedules are unchanged.

### Money Telegram alerts
Enable Telegram Money alerts and Save alerts. Default change threshold is 20%, ATM window ±3 and alert strike step 100; ±2 and step 50 are supported. Alerts aggregate CE/PE Money over the exact seven (or five) strike levels, independently of table Top 3/5 filters. ATM rounds spot to the alert strike step. Collections request a wider chain when needed; incomplete windows do not notify. The first fresh complete collection sends a reference. CE and PE independently notify on threshold increases/decreases relative to their own last alert reference, negative crossings/recovery, or a qualifying CE/PE lead change (margin versus larger side). UI reloads and old snapshots never notify. References are isolated by underlying, resolved expiry, step, window and multiplier, and survive restarts. A changed ATM center uses the same reference for that instrument/expiry/window. Delivery attempts reserve references before network I/O; uncertain or failed sends are shown and not automatically retried, to avoid duplicates. Money alerts do not use MACD's once-per-day restriction: another threshold move can alert again the same day. Demo never sends. Existing .env Telegram credentials are reused.

### Shared Money controls and manual Telegram report
Telegram controls are beside the instrument settings. Difference/alert %, ATM window and strike step are shared with the paired table; Save view & alerts persists them. Top 3/5/All is a presentation selection; automatic reference totals continue to cover the full configured ATM window. Send alert now sends the displayed saved snapshot's highest CE/PE money strikes (union of top N per side, or all window strikes). It names the underlying, expiry, exact window, Money units, per-strike values/views, totals and saved collection time. It neither fetches quotes nor changes automatic alert references. It works when automatic alerts are off, sends available complete CE/PE pairs with a PARTIAL WINDOW warning if strikes are missing, and refuses duplicates of the same snapshot/selection across restarts. Demo never sends; ambiguous delivery failures are not retried automatically. Display search text is not a Telegram filter. Money collection expands to cover the saved shared window whether automatic alerts are on or off.

Money display/alert separation: Top strikes filters the table across all saved strikes matching Strike step. Alert range (beside Telegram controls) limits only automatic alerts and manual Telegram images to ATM ±2/±3. Manual Telegram images include all available complete strike pairs in that window, independently of table Top 3/5/All.
