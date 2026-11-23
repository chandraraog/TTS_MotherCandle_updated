import argparse
import json
import subprocess
import time
from pathlib import Path

CONFIG_FILE = Path(__file__).with_name("config.json")

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Config file not found: {CONFIG_FILE}")
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

def run_all():
    config = load_config()
    processes = []

    for symbol, details in config.items():
        if symbol == "settings":
            continue  # global alert-type toggles, not a stock symbol

        if not details.get("enabled", True):
            continue

        timeframes = details.get("timeframes", {})
        for tf in timeframes.keys():
            print(f"[Starting] {symbol} @ {tf}m timeframe...")
            
            # Spawns bot process per symbol and timeframe
            cmd = [
                "python", "cycle_bot.py",
                "--symbol", symbol,
                "--res", str(tf),
                "--loop"
            ]
            p = subprocess.Popen(cmd)
            processes.append(p)
            time.sleep(1)  # Stagger startup to avoid API throttling

    # Keep master process alive
    for p in processes:
        p.wait()

if __name__ == "__main__":
    run_all()