# Astro Focus V3
Research dashboard only. Mumbai / IST / Lahiri / mean nodes. No orders or execution.

Windows PowerShell: `python -m pip install -r requirements.txt`, then `python focus.py 2026 11`, `python focus.py 2026 12`, or `python focus.py --rolling`. Launch `python -m streamlit run dashboard.py`. Annual generation: `python focus.py --year 2027`.

python -m research.bootstrap --download
python -m streamlit run dashboard.py

Import source CSVs into data/YYYY-MM/events.csv (see December example) and optional positions.csv (see positions_template.csv). All timestamps are local IST without offsets. Positions must be nine sidereal longitudes in degrees from Drik Panchang. Use Mumbai location and verify page header. Prokerala exact aspects must be converted from the published timezone; do not use its tropical longitudes as sidereal positions. Source adapters are manual import only; generation does not fetch websites. November is a small seed, December legacy partial data, not a complete Mumbai calendar. Empty categories are unknown. Snapshot detections are not exact intervals. No exchange holiday calendar or validated forecasts. Monthly generation preserves cross-month intervals from all imported archives. Schedule --rolling via Windows Task Scheduler on the 25th if desired; no schedule is installed.

## Daily Timing Candles
Choose Today, Tomorrow or Choose date in the dashboard and click Fetch selected day timings. The original provider requests only the selected date's month and filters to that date. No adjacent-month requests or automatic monthly timing fetches. Website timestamps are preserved, not certified IST; verify the source timezone. Monthly calendar generation is independent. Original standalone command: python timing_candles.py 2026-11-16. Live provider date parameters and parser remain unverified.

## Custom web dashboard
Install `python -m pip install -r requirements.txt`, then run `start_dashboard.bat` or `python -m uvicorn app:app --host 127.0.0.1 --port 8502`. Open http://localhost:8502. The original Streamlit dashboard remains optional. Generate calendar uses existing imported data, not live Drik retrieval. Daily timing fetch uses only the selected date's month via the original provider; website timezone is unverified. No requests to Prokerala on startup. Keep this local; no authentication is included.

### Trayodashi
November and December 2026 contain exact Mumbai IST intervals for both Pakshas. For future years run `python trayodashi.py 2027` then `python focus.py --rolling` (or select a month). The annual importer validates Mumbai, year and expected annual record count before replacing Trayodashi rows; live HTML parsing has not been verified. No automatic network call is made during dashboard refresh. Other Tithi coverage remains partial.

## NSE regular-session filter
Focus and Positional views exclude weekends, official equity holidays and exact events outside 09:15–15:30 IST. Date-only records are treated as observations on that date, not exact times. Original astronomical intervals are retained; eligible dates appear in notes. All Events and requested daily timings remain unfiltered. Special sessions (including Muhurat) are excluded. The 2026 calendar is bundled from NSE; for future years add market_calendars/YYYY.json with year, source and holidays (ISO dates), verified against NSE. Missing calendars suppress focus results rather than assume weekdays are open. Commodity calendars are not implemented.

## V4 Historical Research

Open Event History or View History from a focus event. Default 10 instances, NIFTY 50. Select Fetch / cache market history to download Yahoo Finance daily OHLC (internet required; not NSE-verified). A sparse, source-labelled NIFTY 50 starter cache is bundled for the ten Mars–Jupiter event windows, from a published historical OHLC mirror. It is not independently NSE-verified. Download continuous history for other events, longer horizons and baseline comparisons. For official data, download continuous daily OHLC from NSE Indices and save a normalized CSV in `data/market_history/nifty_50.csv` or `banknifty.csv` with columns `date,open,high,low,close,source,status` (ISO dates, numeric prices). Include all sessions from before the earliest event through at least 10 sessions after the latest event. No synthetic prices. Existing cached rows are preserved on download.

Mars–Jupiter event dates are bundled, calculated using Swiss Ephemeris apparent geocentric ecliptic longitude (Moshier), not yet cross-checked against Prokerala. The last ten are 2004–2024. To add another event, create `data/event_history/<normalized_event_name>.csv` with `start_ist,end_ist,source,status`; use ISO local timestamps, blank end for exact events. Event catalog uses existing monthly event names; normalization lowercases and replaces punctuation/spaces with underscores. Mars Jupiter aliases map to mars_jupiter. Other events require historical imports. Optional regeneration: install pyswisseph and run `python -m research.seed_events`.

Post mode maps exact after-close/weekend events to the next available session; during-session events use the same session (daily OHLC includes pre-event prices). Interval events use first overlapping session. Event-day mode excludes non-session dates. Daily data must be continuous; gaps can distort session mapping. Baseline is all available sessions in the study date span, including event sessions. No trading execution or predictive validation.

## V4.1 Lunar research
Four lunar interval catalogs are bundled (2020–2026). Generate with `python -m research.lunar_events`. Dates are calculated using Swiss Ephemeris, not yet independently checked against Drik Panchang. Select Event History, choose a lunar event and Last 50, then Fetch / cache market history. The existing sparse conjunction OHLC cache is insufficient for lunar research; no lunar performance figures are bundled. The downloader requires internet access. Both session modes use the first regular session overlapping the Tithi for these four events; no overlap means excluded. The sample is the last 50 completed Tithis, not necessarily 50 matched sessions. Three-session reversal compares the three sessions ending before the observation session with the three sessions after it. Daily OHLC cannot isolate intraday Tithi effects. Verify prices against NSE and dates against Drik before relying on results.

## V4.2 Smart Research
Focus Dates: select a row marked View History · N to open its research; no duplicate event selector. N counts matched NIFTY observations under the default mapping, not event dates. The separate Event History tab lists imported catalogs, including those awaiting market prices. New Moon–Mars, Moon–Jupiter, Moon–Saturn and Moon–Venus catalogs use calculated Swiss Ephemeris times, pending Prokerala verification. Install requirements and fetch continuous market OHLC once using the oldest lunar or Moon conjunction sample; cached prices are shared. The bundled sparse price cache only supports Mars–Jupiter initially. No lunar or Moon-conjunction returns are fabricated. Yahoo Finance is optional and not NSE-verified.

### V4.3 History links
Focus Dates now uses clickable History links instead of row selection checkboxes. Links appear only for matched market observations. Streamlit links reload the same page with the selected event and current filters in the URL; FastAPI opens the existing history tab.

### V4.5 shared research initialization
Run `python -m pip install -r requirements.txt` then `python -m research.bootstrap --download` once to fetch daily NIFTY OHLC and export all nine studies. Alternatively download official NSE Indices daily OHLC CSV and run `python -m research.bootstrap --csv "C:\path\NIFTY.csv"`. Restart Streamlit. History links appear only for matched observations. The bundled cache remains sparse; no new actual returns are claimed until import succeeds. Provider data and calculated planetary dates require independent verification. Existing native dataframe is unchanged.

## V4.6 positional research
Adds Saturn/Jupiter sign crossings and separate direct/retrograde stations, Mars–mean Rahu exact and within 3 degrees, Mars one sign behind Saturn, and Mercury combustion. Catalogs calculated with Swiss Ephemeris, Lahiri, 1995–2026; source cross-check pending. Mercury uses a fixed 14-degree separation, not Drik's published Asta definition; duration groups are exploratory. Sign crossings include retrograde re-entries. Catalog generation uses a two-day scan and root refinement; very short excursions may be missed. Full interval return uses previous close before first overlapping session to last overlapping close. Daily OHLC cannot isolate intraday effects. Next-session returns start at the observation close. Setup.bat installs and downloads shared OHLC; existing sparse cache cannot support positional studies. No completed positional market results are bundled. Run bootstrap with official CSV or download. Preserve local market CSV when upgrading.

V4.6 defaults: Saturn sign crossings up to 50 (all bundled completed crossings), Mercury combustion 30, Jupiter crossings and stations 20, Mars positional studies 10. Download requests history from December 1994; provider coverage may start later.

### V4.6.1 — Inline history
Streamlit: click the blue History cell in the native Focus Dates dataframe. Single-cell selection (Streamlit >=1.56) opens the research expander directly below the table; no URL navigation or row checkbox. Close history clears selection. The separate Event History tab is unchanged. FastAPI history links likewise display the research panel beneath the calendar. Existing data and research algorithms are unchanged. Preserve your local continuous market cache when upgrading.

## V4.7 — Nakshatra research
Six new catalogs (2014–2026), default 100 completed intervals: Moon in Moola, Pushya, Uttara Phalguni, Jyeshtha, Ashwini Pada 3, and Gandamool (merged adjacent pairs). Calculated Lahiri sidereal Moon longitude; Drik Panchang verification pending. First overlapping regular session only; intervals entirely outside sessions excluded. Gandamool overlaps individual nakshatras and is not independent evidence. Run `python -m research.nakshatra_events` to regenerate. Preserve your existing data/market_history files when upgrading; run `python -m research.bootstrap` to calculate using local prices or `--download` to fetch. Sparse bundled cache cannot complete these studies. Existing native dataframe and inline history retained.

## V4.7.2 — Month navigation
Select year (2000–2050) and month independently; defaults to current IST month. Previous/Next cross year boundaries. Current resets to IST month. Generate/refresh rebuilds from imported source records, not a live planetary download. For a new month import verified records into data/YYYY-MM/events.csv (same schema as existing archives); add verified annual NSE holidays in market_calendars/YYYY.json before Focus Dates can be produced. All Events remains available without a market calendar. Missing records are not evidence of no events.

### V4.7.6 — Mumbai IST validation
Validates the actual Prokerala monthly introduction and Mumbai form location ID/name. October 2026 validated HTML from the supplied debug response is bundled in data/timing_cache; CLI tested offline for October 1 (7 supported aspects). Other months still use the existing POST fetch. Live requests for other months and browser UI are not tested in this environment.


## V4.7.7 — User inference annotations

- Adds an `I` information marker immediately after the Event column in event tables when a supplied inference rule matches.
- Hover tooltip text shows the user's stated market hypothesis and applicable market (Index, Gold, or Silver).
- Adds an `Inference rules` dashboard tab and an `Inference Rules` worksheet to monthly Excel exports.
- Rules are preserved as user-supplied hypotheses, not validated trading signals.
- A rule is shown only when the event record contains the required evidence. For combined retrograde rules, overlapping intervals must be present in the loaded event records.
- If a sign, weekday, exact conjunction, or retrograde overlap is not available in the event data, the marker is not shown; the app does not invent missing conditions.


## V4.7.8 — Focus table rendering fix
- Removed Pandas Styler tooltip injection that was leaking raw `<span class="pd-t">` markup into Streamlit dataframe cells.
- Inference details now open in an explicit expandable panel when the user selects an `I` cell; no HTML tooltip dependency.
- Focus Dates uses a compact set of columns and explicit widths so key fields are easier to scan.
- Preserved current-day row highlighting, History cell selection, inference rules, lookup, event history, and timing-candle modules.
- Browser-level rendering still requires verification in the user's Streamlit environment.


## V4.7.9 — Focus Dates table sizing
- Focus Dates native Streamlit dataframe now uses an explicit desktop column-width layout to reduce unused horizontal space.
- Added Small / Medium / Large / Extra Large table font-size control (default Medium).
- Retains native sorting, column controls, History selection and inference detail selection.
- Narrow displays may use horizontal scrolling; actual browser rendering should be checked in the user's Streamlit environment.


## V4.7.10 — Focus Dates zoom controls
- Replaced the font-size slider with visible − / + buttons and a Reset control.
- Font size adjusts from 10px to 24px in 2px steps; default is 14px.
- Retains the native Streamlit dataframe and existing selection/history/inference interactions.


## V4.7.12 — Zoom button compatibility fix
- Removed unsupported `size='small'` arguments from Streamlit zoom buttons.
- Kept compact inline minus/plus controls and the existing font-size bounds.


## V4.7.14 — automatic Focus Dates column sizing
- Removed hard-coded pixel widths from Focus Dates columns.
- Native Streamlit dataframe now sizes columns using its own content/layout behaviour within the stretched available width.
- Preserved History and inference cell selection; no font-size controls or CSS sizing overrides.
- Actual browser layout remains subject to the installed Streamlit version and viewport.
