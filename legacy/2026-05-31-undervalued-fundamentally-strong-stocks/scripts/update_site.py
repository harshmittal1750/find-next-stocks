#!/usr/bin/env python3
"""Build and validate the JSON-backed static dashboard.

The former implementation extracted ``const DATA = [...]`` from generated HTML
and spliced it into ``site/index.html``. That made a large HTML file act as both
database and UI, and a formatting change could break the parser. The dashboard
builder now writes ``site/dashboard-data.json`` directly; both HTML entry points
load that file with ``fetch``.

Usage:  python3 scripts/update_site.py [--no-rebuild]
"""
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA_JSON = BASE / "site" / "dashboard-data.json"


def validate_payload(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"ERROR: {path} does not exist; run build_dashboard.py")
    except json.JSONDecodeError as exc:
        sys.exit(f"ERROR: invalid JSON in {path}: {exc}")

    if payload.get("schema_version") != 1:
        sys.exit("ERROR: dashboard JSON must have schema_version = 1")
    stocks = payload.get("stocks")
    if not isinstance(stocks, list) or not stocks:
        sys.exit("ERROR: dashboard JSON must contain a non-empty stocks array")
    declared = payload.get("record_count")
    if declared != len(stocks):
        sys.exit(f"ERROR: record_count={declared} but stocks contains {len(stocks)} records")
    tickers = [row.get("ticker") for row in stocks]
    if any(not ticker for ticker in tickers) or len(tickers) != len(set(tickers)):
        sys.exit("ERROR: every dashboard stock must have a unique non-empty ticker")
    return payload


def main():
    if "--no-rebuild" not in sys.argv:
        py = BASE / ".venv" / "bin" / "python"
        interpreter = str(py) if py.exists() else sys.executable
        print("rebuilding DASHBOARD.html and site/dashboard-data.json ...")
        result = subprocess.run([interpreter, str(BASE / "scripts" / "build_dashboard.py")])
        if result.returncode != 0:
            sys.exit("ERROR: build_dashboard.py failed")

    payload = validate_payload(DATA_JSON)
    print(f"OK: validated {len(payload['stocks'])} JSON-backed stocks in "
          f"{DATA_JSON.relative_to(BASE)}")


if __name__ == "__main__":
    main()
