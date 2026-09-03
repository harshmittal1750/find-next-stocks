from __future__ import annotations

import os
from pathlib import Path


def repository_root() -> Path:
    configured = os.getenv("FIND_NEXT_STOCKS_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[4]


ROOT = repository_root()
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
EXPORT_DIR = DATA_DIR / "exports"
WAREHOUSE_DIR = DATA_DIR / "warehouse"
