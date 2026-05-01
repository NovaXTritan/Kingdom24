"""Add the repo root to sys.path and pin tests to the local seed catalog.

We set PRODUCTS_JSON_PATH to the 32-SKU seed before any module imports happen
so search / scoring assertions are deterministic regardless of whether the
website's 526-SKU catalog is also present on disk.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Pin tests to the seed catalog
os.environ.setdefault("PRODUCTS_JSON_PATH", str(ROOT / "data" / "products.json"))
