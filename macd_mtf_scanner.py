"""Compatibility launcher: implementation moved to OIDataV2."""
from pathlib import Path
import runpy
import sys

if __name__ == "__main__":
    folder = Path(__file__).resolve().parent / "OIDataV2"
    sys.path.insert(0, str(folder))
    runpy.run_path(str(folder / "macd_mtf_scanner.py"), run_name="__main__")
