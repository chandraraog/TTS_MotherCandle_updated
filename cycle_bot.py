from __future__ import annotations

import argparse
import json
import time as time_module
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from fyers_client import get_fyers
from alerts import send_alert
from utils import canonical_bar_index, bar_index_to_datetime
import cycle_state

# ============================================================
# BOT CONFIGURATION
# ============================================================

IST = "Asia/Kolkata"

CONVERGENCE_TOLERANCE_MIN = 0  # exact-candle match; series share the same resolution grid

# Bootstrap (one-time catch-up) settings, used only when a saved
# reference point is older than the small recent-history window.
BOOTSTRAP_CHUNK_DAYS = 60
BOOTSTRAP_MAX_CHUNKS = 80

# ------------------------------------------------------------
# TIME PRICE SQUARE (TPS)
# ------------------------------------------------------------
TPS_TOLERANCE = 0.0
TPS_LOOKBACK_EVENTS = 3

_last_printed_console_key: str = ""


# ============================================================
# CONFIG LOADING
# ============================================================

def load_origin_config(symbol: str, resolution: str) -> Optional[dict]:
    """
    Returns the raw timeframe config block, e.g.:
        {"A": "...", "B": "...", "C": "...",
         "AB_bars": 42, "BC_bars": 21, "AC_bars": 63}
    AB_bars / BC_bars / AC_bars are optional - if present, they are
    used as-is (exact bar counts, e.g. read straight off TradingView's
    cyclic line tool). If absent, an approximate weekday-only estimate
    is used just to seed the very first bootstrap, and a warning is
    printed since it won't account for exchange holidays.
    """
    config_file = Path(__file__).with_name("config.json")
    if not config_file.exists():
        return None

    try:
        data = json.loads(config_file.read_text(encoding="utf-8"))
        tf_data = data.get(symbol, {}).get("timeframes", {}).get(resolution, {})
        if tf_data.get("A") and tf_data.get("B") and tf_data.get("C"):
            return tf_data
    except Exception as exc:
        print(f"[Error] Failed to read origin from config.json: {exc}")

    return None


def load_alert_settings(tf_cfg: dict) -> tuple[bool, bool]:
    """
    Returns (alert_mother_candle_enabled, alert_tps_enabled).

    A top-level "settings" block in config.json sets the default for
    every symbol/timeframe:
        "settings": {"alert_mother_candle": true, "alert_tps": true}
    A per-timeframe "alert_mother_candle" / "alert_tps" key overrides
    the default for just that one entry, e.g. to silence TPS alerts
    for a single noisy symbol while keeping Mother Candle on.
    """
    mother, tps = True, True

    config_file = Path(__file__).with_name("config.json")
    try:
        data = json.loads(config_file.read_text(encoding="utf-8"))
        global_settings = data.get("settings", {})
        mother = bool(global_settings.get("alert_mother_candle", True))
        tps = bool(global_settings.get("alert_tps", True))
    except Exception as exc:
        print(f"[Warning] Could not read global alert settings: {exc}")

    if "alert_mother_candle" in tf_cfg:
        mother = bool(tf_cfg["alert_mother_candle"])
    if "alert_tps" in tf_cfg:
        tps = bool(tf_cfg["alert_tps"])

    return mother, tps


def resolve_bar_lengths(cfg: dict, resolution_minutes: int) -> tuple[int, int, int]:
    a_time = datetime.strptime(cfg["A"], "%Y-%m-%d %H:%M")
    b_time = datetime.strptime(cfg["B"], "%Y-%m-%d %H:%M")
    c_time = datetime.strptime(cfg["C"], "%Y-%m-%d %H:%M")

    if "AB_bars" in cfg and "BC_bars" in cfg and "AC_bars" in cfg:
        return int(cfg["AB_bars"]), int(cfg["BC_bars"]), int(cfg["AC_bars"])

    print(
        "[Warning] AB_bars/BC_bars/AC_bars not set in config.json for this symbol/timeframe. "
        "Falling back to a weekday-only estimate (will NOT account for exchange holidays). "
        "For exact results, read the bar counts off TradingView's cyclic line tool and add "
        "\"AB_bars\", \"BC_bars\", \"AC_bars\" to config.json."
    )
    a_idx = canonical_bar_index(a_time, resolution_minutes)
    b_idx = canonical_bar_index(b_time, resolution_minutes)
    c_idx = canonical_bar_index(c_time, resolution_minutes)
    return b_idx - a_idx, c_idx - b_idx, c_idx - a_idx


# ============================================================
# REAL-BAR ADVANCEMENT (holiday-safe: holidays simply have no row)
# ============================================================

def find_position_by_time(df: pd.DataFrame, dt: datetime) -> Optional[int]:
    matches = df.index[df["datetime"] == dt]
    return int(matches[0]) if len(matches) else None


def advance_series_with_df(
    df: pd.DataFrame,
    ref_time: datetime,
    bar_length: int,
    now: datetime,
) -> tuple[datetime, list[dict], bool]:
    """
    Starting from `ref_time`, hop forward by exactly `bar_length` REAL
    candle rows at a time (using this df's own row order - so holidays,
    which simply don't have a row, are automatically skipped). Stops
    when the next hop would land past the last CLOSED candle in `df`,
    or past `now`.

    Returns (final_ref_time, new_hops, found_in_df):
        new_hops   - list of {"datetime", "high", "low"} for every
                     intermediate closed occurrence reached this call
        found_in_df - False if `ref_time` itself isn't present in `df`
                      at all (caller should bootstrap from an older
                      fetch in that case)
    """
    pos = find_position_by_time(df, ref_time)
    if pos is None:
        return ref_time, [], False

    last_closed_pos = len(df) - 2  # don't trust the still-forming last row
    hops: list[dict] = []

    while True:
        next_pos = pos + bar_length
        if next_pos > last_closed_pos:
            break

        row = df.iloc[next_pos]
        next_time = row["datetime"].to_pydatetime()
        if next_time > now:
            break

        pos = next_pos
        ref_time = next_time
        hops.append({
            "datetime": ref_time.strftime("%Y-%m-%d %H:%M"),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        })

    return ref_time, hops, True


def bootstrap_advance(
    symbol: str,
    resolution: str,
    ref_time: datetime,
    bar_length: int,
    now: datetime,
    catch_up_target_days: int,
) -> tuple[datetime, list[dict]]:
    """
    One-time catch-up: fetch a chunk of history starting at `ref_time`,
    real-bar-hop forward through it, then repeat with the next chunk
    until `ref_time` is inside `catch_up_target_days` of "now" - i.e.
    inside the window the normal small-window fetch (`--days-back`)
    will cover on every future run, so bootstrap never has to run
    again for this series. Only runs when the saved reference has
    fallen out of the currently loaded recent-history window
    (typically: very first run for a symbol, or the bot was offline
    for a long stretch).
    """
    all_hops: list[dict] = []
    chunk_days = BOOTSTRAP_CHUNK_DAYS

    for _ in range(BOOTSTRAP_MAX_CHUNKS):
        if ref_time >= now - timedelta(days=catch_up_target_days):
            break

        chunk_start = ref_time.date()
        chunk_end = min(now.date(), ref_time.date() + timedelta(days=chunk_days * 2))

        try:
            chunk_df = fetch_history(symbol, resolution, chunk_start.isoformat(), chunk_end.isoformat())
        except Exception as exc:
            print(f"[Bootstrap] {symbol} [{resolution}m] fetch failed for {chunk_start}: {exc}")
            break

        if len(chunk_df) < 2:
            break

        new_ref, hops, found = advance_series_with_df(chunk_df, ref_time, bar_length, now)

        if not found:
            print(
                f"[Bootstrap] {symbol} [{resolution}m] reference {ref_time:%Y-%m-%d %H:%M} "
                f"not found in fetched history - it may be outside what the broker provides for this timeframe."
            )
            break

        all_hops.extend(hops)

        if new_ref == ref_time:
            # No progress in this chunk (bar_length spans further than the
            # chunk, or we're near the end of available history) - widen
            # the FETCH window only; the catch-up target stays fixed.
            chunk_days = min(chunk_days * 2, 400)
            if chunk_days >= 400:
                break
            continue

        ref_time = new_ref
        chunk_days = BOOTSTRAP_CHUNK_DAYS  # reset back down once progress resumes

    return ref_time, all_hops


# ============================================================
# EVENT HISTORY MERGING / CLASSIFICATION
# ============================================================

def merge_hops_into_history(history: list[dict], tag: str, hops: list[dict]) -> list[dict]:
    """
    Merge freshly-reached hops (from one series, e.g. "AB") into the
    persisted event history, clustering entries that land on the exact
    same candle (so AB+BC hitting the same time become one entry with
    both tags = a Convergence/Mother Candle).
    """
    by_time = {e["datetime"]: e for e in history}

    for hop in hops:
        key = hop["datetime"]
        if key in by_time:
            entry = by_time[key]
            if tag not in entry["tags"]:
                entry["tags"].append(tag)
        else:
            entry = {
                "datetime": key,
                "open": hop["open"],
                "high": hop["high"],
                "low": hop["low"],
                "close": hop["close"],
                "tags": [tag],
            }
            by_time[key] = entry
            history.append(entry)

    history.sort(key=lambda e: e["datetime"])
    return history


def classify(tags: list[str]) -> str:
    count = len(tags)
    if count >= 3:
        return "Mother Candle - 3 Lines"
    if count == 2:
        return "Convergence - 2 Lines"
    return "Normal Cycle (Single Line)"


def detect_tps(history: list[dict], current_pos: int, tolerance: float = TPS_TOLERANCE, lookback: int = TPS_LOOKBACK_EVENTS) -> dict:
    current = history[current_pos]
    current_high, current_low = current["high"], current["low"]

    matches = []
    for back in range(1, min(current_pos, lookback) + 1):
        prev = history[current_pos - back]
        previous_high, previous_low = prev["high"], prev["low"]

        high_match = abs(current_high - previous_high) <= tolerance
        low_match = abs(current_low - previous_low) <= tolerance
        high_touch = current_low <= previous_high <= current_high
        low_touch = current_low <= previous_low <= current_high

        if high_match or low_match or high_touch or low_touch:
            matches.append({
                "cycle": back,
                "datetime": prev["datetime"],
                "high": previous_high,
                "low": previous_low,
                "reason": {
                    "high_match": high_match,
                    "low_match": low_match,
                    "high_touch": high_touch,
                    "low_touch": low_touch,
                },
            })

    return {"is_tps": len(matches) > 0, "strength": len(matches), "matches": matches}


# ============================================================
# DATA FETCH
# ============================================================

def fetch_history(symbol: str, resolution: str, start_date: str, end_date: str) -> pd.DataFrame:
    fyers = get_fyers()

    request = {
        "symbol": symbol,
        "resolution": resolution,
        "date_format": "1",
        "range_from": start_date,
        "range_to": end_date,
        "cont_flag": "1",
    }

    response = fyers.history(request)
    candles = response.get("candles")
    if not candles:
        raise RuntimeError(f"FYERS history error: {response}")

    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])

    df["datetime"] = (
        pd.to_datetime(df["timestamp"], unit="s", utc=True)
        .dt.tz_convert(IST)
        .dt.tz_localize(None)
    )

    return df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)


# ============================================================
# ALERTING
# ============================================================

def get_alert_filepath(symbol: str, resolution: str) -> Path:
    clean_sym = symbol.replace(":", "_").replace("-", "_")
    return Path(__file__).with_name(f"last_alert_{clean_sym}_{resolution}m.txt")


def emit_alert_if_new(symbol: str, resolution: str, alert_key: str, message: str, types: list[str]) -> bool:
    alert_file = get_alert_filepath(symbol, resolution)
    last_key = alert_file.read_text(encoding="utf-8").strip() if alert_file.exists() else ""

    if last_key == alert_key:
        return False

    send_alert(message, resolution=resolution, types=types)
    alert_file.write_text(alert_key.strip(), encoding="utf-8")
    return True


def build_alert_message(symbol, resolution, active_time_str, active_open, active_high, active_low, active_close, is_mother, is_tps) -> str:
    tags = []
    if is_mother:
        tags.append("MC")
    if is_tps:
        tags.append("TPS")
    tag = "+".join(tags) if tags else "?"

    diff = active_high - active_low

    return (
        f"TIME AND PRICE SQUARED! ({tag})\n"
        f"{symbol} @ candle time {active_time_str} IST ({resolution}m)\n"
        f"OPEN: {active_open:.2f}    HIGH: {active_high:.2f}\n"
        f"LOW: {active_low:.2f}    CLOSE: {active_close:.2f}\n"
        f"DIFF: {diff:.2f}"
    )


# ============================================================
# MAIN CYCLE
# ============================================================

def run_once(symbol: str, resolution: str, days_back: int) -> None:
    global _last_printed_console_key

    res_mins = int(resolution)
    cfg = load_origin_config(symbol, resolution)
    if not cfg:
        print(f"[{symbol} {resolution}m] No origin configured in config.json.")
        return

    state = cycle_state.load_state(symbol, resolution)
    if state is None:
        len_ab, len_bc, len_ac = resolve_bar_lengths(cfg, res_mins)
        state = {
            "len_ab": len_ab, "len_bc": len_bc, "len_ac": len_ac,
            "last_ab_ref": cfg["A"], "last_bc_ref": cfg["B"], "last_ac_ref": cfg["A"],
            "event_history": [],
        }

    now = datetime.now()
    today = now.date()
    start_date = today - timedelta(days=days_back)
    df = fetch_history(symbol, resolution, start_date.isoformat(), today.isoformat())

    if len(df) < 2:
        cycle_state.save_state(symbol, resolution, state)
        return

    history = state.get("event_history", [])

    for tag, len_key, ref_key in (("AB", "len_ab", "last_ab_ref"), ("BC", "len_bc", "last_bc_ref"), ("AC", "len_ac", "last_ac_ref")):
        ref_time = datetime.strptime(state[ref_key], "%Y-%m-%d %H:%M")
        bar_length = int(state[len_key])

        new_ref, hops, found = advance_series_with_df(df, ref_time, bar_length, now)

        if not found:
            # Reference fell outside the small recent window - catch up once,
            # all the way into the days_back window so future runs stay cheap.
            new_ref, boot_hops = bootstrap_advance(symbol, resolution, ref_time, bar_length, now, days_back)
            history = merge_hops_into_history(history, tag, boot_hops)
            # Try once more against the main df in case bootstrap landed us back in range.
            new_ref2, hops2, found2 = advance_series_with_df(df, new_ref, bar_length, now)
            if found2:
                new_ref = new_ref2
                hops = hops2
            else:
                hops = []

        history = merge_hops_into_history(history, tag, hops)
        state[ref_key] = new_ref.strftime("%Y-%m-%d %H:%M")

    state["event_history"] = history
    cycle_state.save_state(symbol, resolution, state)

    active_time_str = "N/A"
    active_open, active_high, active_low, active_close = 0.0, 0.0, 0.0, 0.0
    active_type = "Normal Cycle (Single Line)"
    current_cycle_key = ""
    is_mother = False
    tps = {"is_tps": False, "strength": 0, "matches": []}

    if history:
        last_pos = len(history) - 1
        last_e = history[last_pos]
        active_time_str = last_e["datetime"]
        current_cycle_key = active_time_str
        active_type = classify(last_e["tags"])
        is_mother = len(last_e["tags"]) >= 3
        active_high = last_e["high"]
        active_low = last_e["low"]
        # Fallback for state files saved before open/close were tracked.
        active_open = last_e.get("open", active_low)
        active_close = last_e.get("close", active_high)
        tps = detect_tps(history, last_pos, TPS_TOLERANCE, TPS_LOOKBACK_EVENTS)

    # "Next upcoming" - approximate ETA only (for display), from the
    # nearest series' next multiple. Not used for alerting/classification.
    next_candidates = []
    for len_key, ref_key in (("len_ab", "last_ab_ref"), ("len_bc", "last_bc_ref"), ("len_ac", "last_ac_ref")):
        ref_time = datetime.strptime(state[ref_key], "%Y-%m-%d %H:%M")
        approx_next = bar_index_to_datetime(
            canonical_bar_index(ref_time, res_mins) + int(state[len_key]), res_mins
        )
        next_candidates.append(approx_next)
    next_cycle_str = f"{min(next_candidates):%Y-%m-%d %H:%M} (approx)" if next_candidates else "N/A"

    a_txt = cfg["A"]
    b_txt = cfg["B"]
    c_txt = cfg["C"]
    now_str = now.strftime("%H:%M:%S")

    if current_cycle_key and current_cycle_key != _last_printed_console_key:
        _last_printed_console_key = current_cycle_key
        tps_line = f"YES ({tps['strength']}/{TPS_LOOKBACK_EVENTS})" if tps["is_tps"] else "No"

        print(
            f"\n🚨 Closed Cycle Event ({symbol} - {resolution}m)\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"• Symbol : {symbol}\n"
            f"• Timeframe : {resolution}m\n"
            "📌 Origin (A → B → C)\n"
            f"• A : {a_txt}\n"
            f"• B : {b_txt}\n"
            f"• C : {c_txt}\n"
            f"• Bars  AB={state['len_ab']}  BC={state['len_bc']}  AC={state['len_ac']}\n\n"
            "📊 Active Cycle Line (Closed Candle)\n"
            f"• Time   : {active_time_str}\n"
            f"• High   : {active_high:.2f}\n"
            f"• Low    : {active_low:.2f}\n"
            f"• Type   : {active_type}\n"
            f"• TPS    : {tps_line}\n\n"
            "🎯 Next Upcoming Cycle\n"
            f"• Target : {next_cycle_str}\n"
        )
    else:
        print(f"[{now_str}] {symbol} [{resolution}m] Heartbeat... (Closed Cycle: {active_time_str})", end="\r")

    # ------------------------------------------------------------
    # ALERT FILTER: only Mother Candle OR TPS gets through, and only
    # for the types enabled in config.json's "settings" block (or a
    # per-timeframe override).
    # ------------------------------------------------------------
    alert_mother_enabled, alert_tps_enabled = load_alert_settings(cfg)
    should_alert = (is_mother and alert_mother_enabled) or (tps["is_tps"] and alert_tps_enabled)

    if should_alert and current_cycle_key:
        message = build_alert_message(symbol, resolution, active_time_str, active_open, active_high, active_low, active_close, is_mother, tps["is_tps"])
        triggered_types = []
        if is_mother and alert_mother_enabled:
            triggered_types.append("MC")
        if tps["is_tps"] and alert_tps_enabled:
            triggered_types.append("TPS")
        sent = emit_alert_if_new(symbol, resolution, current_cycle_key, message, triggered_types)
        if sent:
            print(f"\n[Alert] Dispatched for {symbol} [{resolution}m] at {current_cycle_key}")
    elif current_cycle_key:
        print(f"[Discarded] {symbol} [{resolution}m] {active_time_str} - {active_type}, no TPS. Skipping alert.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-Asset Advanced Cycle Bot Engine (Mother Candle + TPS)")
    parser.add_argument("--symbol", type=str, required=True)
    parser.add_argument("--res", type=str, required=True)
    parser.add_argument("--days-back", type=int, default=15, help="How many recent calendar days of history to fetch each run")
    parser.add_argument("--loop", action="store_true")

    args = parser.parse_args()

    if not args.loop:
        run_once(args.symbol, args.res, args.days_back)
        return

    sleep_sec = int(args.res) * 60
    while True:
        try:
            run_once(args.symbol, args.res, args.days_back)
        except Exception as exc:
            print(f"\n[{args.symbol} {args.res}m ERROR] {exc}")
        time_module.sleep(sleep_sec)


if __name__ == "__main__":
    main()
