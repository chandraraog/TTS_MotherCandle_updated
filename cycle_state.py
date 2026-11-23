"""
Local reference-point storage
==============================
Keeps a small JSON file per (symbol, resolution) so the bot never has
to re-walk all the way back to the original A/B/C origin date. Each
run, we advance the saved reference forward by the exact bar count
using REAL candle rows (holiday-safe by construction - a holiday
just means there's no row for that day), then save the new reference
back here.

File layout: state/<SYMBOL>_<RES>m.json
{
  "len_ab": 42, "len_bc": 21, "len_ac": 63,
  "last_ab_ref": "2026-08-14 13:15",
  "last_bc_ref": "2026-08-12 11:45",
  "last_ac_ref": "2026-08-12 09:15",
  "event_history": [
    {"datetime": "2026-08-14 13:15", "high": 24375.2, "low": 24354.45, "tags": ["AB"]},
    ...
  ]
}
"""

import json
from pathlib import Path
from typing import Optional

STATE_DIR = Path(__file__).with_name("state")
MAX_HISTORY = 30


def _clean(symbol: str) -> str:
    return symbol.replace(":", "_").replace("-", "_")


def state_path(symbol: str, resolution: str) -> Path:
    STATE_DIR.mkdir(exist_ok=True)
    return STATE_DIR / f"{_clean(symbol)}_{resolution}m.json"


def load_state(symbol: str, resolution: str) -> Optional[dict]:
    path = state_path(symbol, resolution)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[State] Failed to read {path.name}: {exc}")
        return None


def save_state(symbol: str, resolution: str, state: dict) -> None:
    state = dict(state)
    state["event_history"] = state.get("event_history", [])[-MAX_HISTORY:]
    path = state_path(symbol, resolution)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
