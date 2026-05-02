"""Product catalog: load, search, format.

Source priority:
  1. settings.PRODUCTS_JSON_PATH (explicit override — tests use this)
  2. ../kingdom24-web/data/products.json (the website's authoritative 526-SKU file)
  3. data/products.json (32-SKU local seed)

Hot reload: stat() the source file every CATALOG_RELOAD_POLL_SECONDS — if mtime
changed, reload. Logs price changes per SKU.

Search: deterministic keyword scorer + Levenshtein typo tolerance for English
tokens (skips short tokens to avoid false matches). Pure Python, no extra deps.
"""

from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import DATA_DIR, WEBSITE_PRODUCTS_JSON, settings
from core.logger import log_event


@dataclass
class Product:
    id: str
    name: str
    name_hi: str
    category: str
    type: str
    price_per_kg: Optional[float]
    price_per_piece: Optional[float]
    moq: int
    moq_unit: str
    pack_sizes: List[str]
    shelf_life_months: int
    storage: str
    description: str
    use_cases: List[str]
    store_link: str
    image_url: str
    keywords: List[str]


# ─── Source resolution ───────────────────────────────────────────
_LOCAL_FALLBACK = DATA_DIR / "products.json"


# In-repo full catalog: the website's products.json copied in at build time.
# This is what makes the 526-SKU catalog available on Render where the sibling
# kingdom24-web folder doesn't exist.
_FULL_CATALOG = DATA_DIR / "products_full.json"


def _resolve_source() -> Path:
    if settings.PRODUCTS_JSON_PATH:
        p = Path(settings.PRODUCTS_JSON_PATH)
        if p.exists():
            return p
    if WEBSITE_PRODUCTS_JSON.exists():
        return WEBSITE_PRODUCTS_JSON
    if _FULL_CATALOG.exists():
        return _FULL_CATALOG
    return _LOCAL_FALLBACK


# ─── Schema adapter — website JSON → chatbot Product ─────────────
def _from_website_record(r: dict) -> Optional[Product]:
    """Map a kingdom24-web product to the chatbot's Product dataclass."""
    name = r.get("name") or r.get("name_clean") or ""
    if not name:
        return None
    price = r.get("price")
    if price is None:
        return None
    storage = "frozen" if (r.get("frozen") == "Frozen") else "ambient"
    return Product(
        id=r.get("slug") or r.get("sku") or name.lower().replace(" ", "-")[:60],
        name=name,
        name_hi="",
        category=r.get("category_slug") or r.get("category") or "uncategorised",
        type="RTE",
        price_per_kg=float(price),
        price_per_piece=None,
        moq=settings.DEFAULT_FROZEN_MOQ_KG if storage == "frozen" else settings.DEFAULT_AMBIENT_MOQ_KG,
        moq_unit="kg",
        pack_sizes=[f"{r.get('weight_value', '1')} {r.get('weight_unit', 'Kg')}"],
        shelf_life_months=int(r.get("shelf_life_months") or 12),
        storage=storage,
        description=(r.get("short_description") or r.get("description") or "")[:300],
        use_cases=["Restaurants", "Hotels", "Cloud Kitchens", "Caterers"],
        store_link=f"https://kingdom24.in/products/{r.get('slug', '')}",
        image_url=r.get("image", ""),
        keywords=[name.lower(), r.get("category", "").lower()],
    )


def _from_chatbot_record(r: dict) -> Optional[Product]:
    """Native chatbot schema (data/products.json)."""
    try:
        return Product(**r)
    except (TypeError, ValueError):
        return None


def _load_from_path(path: Path) -> List[Product]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    products: List[Product] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        # Try chatbot-native first; if shape doesn't match, try website schema
        p = _from_chatbot_record(r)
        if p is None and r.get("slug"):
            p = _from_website_record(r)
        if p:
            products.append(p)
    return products


# ─── Catalog state with hot reload ───────────────────────────────
class _CatalogState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.products: List[Product] = []
        self.source: Path = _LOCAL_FALLBACK
        self.mtime: float = 0.0
        self.last_reload: float = 0.0
        self.last_polled: float = 0.0
        self._price_index: Dict[str, float] = {}

    def load(self) -> None:
        with self._lock:
            src = _resolve_source()
            try:
                mtime = src.stat().st_mtime
            except OSError:
                mtime = 0.0
            new_products = _load_from_path(src)

            # Diff prices for the log
            old_index = self._price_index
            new_index = {p.id: float(p.price_per_kg or 0) for p in new_products}
            changes = []
            for pid, np in new_index.items():
                op = old_index.get(pid)
                if op is not None and abs(np - op) > 0.01:
                    changes.append((pid, op, np))

            self.products = new_products
            self.source = src
            self.mtime = mtime
            self.last_reload = time.time()
            self._price_index = new_index

            log_event(
                "catalog_load",
                source=str(src),
                product_count=len(new_products),
                price_changes=len(changes),
            )
            for pid, op, np in changes[:25]:
                log_event("catalog_price_change", level="INFO", product_id=pid, old=op, new=np)

            if src == _LOCAL_FALLBACK and src.exists():
                log_event(
                    "catalog_using_seed",
                    level="WARNING",
                    note="Falling back to 32-SKU local seed — neither website catalog "
                    f"({WEBSITE_PRODUCTS_JSON}) nor in-repo full catalog "
                    f"({_FULL_CATALOG}) found.",
                )

    def maybe_reload(self) -> bool:
        now = time.time()
        if now - self.last_polled < settings.CATALOG_RELOAD_POLL_SECONDS:
            return False
        self.last_polled = now
        try:
            mtime = _resolve_source().stat().st_mtime
        except OSError:
            return False
        if mtime > self.mtime:
            self.load()
            return True
        return False


_state = _CatalogState()


def _ensure_loaded() -> None:
    if not _state.products:
        _state.load()


def all_products() -> List[Product]:
    _ensure_loaded()
    _state.maybe_reload()
    return list(_state.products)


def by_id(pid: str) -> Optional[Product]:
    for p in all_products():
        if p.id == pid:
            return p
    return None


def categories() -> List[dict]:
    cats: Dict[str, dict] = {}
    for p in all_products():
        if p.category not in cats:
            cats[p.category] = {"category": p.category, "count": 0, "store_link": p.store_link}
        cats[p.category]["count"] += 1
    return list(cats.values())


def to_dict(p: Product) -> dict:
    return asdict(p)


def info() -> dict:
    _ensure_loaded()
    return {
        "product_count": len(_state.products),
        "source": str(_state.source),
        "last_reload_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_state.last_reload)),
        "is_seed": _state.source == _LOCAL_FALLBACK,
    }


# Search relevance threshold — products scoring below this are dropped even if
# they technically matched a token. Prevents the "any word match" false-positive
# (e.g. product description containing "help" matching "I need help"). The
# existing scorer awards 4 for name match, 2 for category/keyword/fuzzy, 1 for
# description-only — this floor cuts pure description hits unless multiple.
MIN_PRODUCT_SCORE = 2


def reload_now() -> None:
    """Force a reload (used in tests / admin endpoints)."""
    _state.load()


# ─── Normalisation + Levenshtein ────────────────────────────────
def _norm(text: str) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    only_ascii = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", only_ascii.lower()).strip()


def levenshtein(a: str, b: str) -> int:
    """Pure-Python Levenshtein distance. ~15 lines, no deps."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if abs(len(a) - len(b)) > 4:
        return 99  # cheap rejection
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[-1]


def _fuzzy_match(token: str, hay: str) -> bool:
    """Token appears in `hay` directly OR is within edit distance 2 of any
    haystack word ≥ 4 chars."""
    if len(token) < 3:
        return token in hay
    if token in hay:
        return True
    for word in re.findall(r"[a-z]{3,}", hay):
        if abs(len(word) - len(token)) > 2:
            continue
        if levenshtein(token, word) <= 2:
            return True
    return False


# ─── Search ─────────────────────────────────────────────────────
def search(
    query: str,
    category: Optional[str] = None,
    storage: Optional[str] = None,
    limit: int = 5,
) -> List[Product]:
    products = all_products()
    if not query:
        out = products
        if category:
            out = [p for p in out if p.category == category]
        if storage:
            out = [p for p in out if p.storage == storage]
        return out[:limit]

    q = _norm(query)
    tokens = [t for t in re.split(r"[\s,;/]+", q) if len(t) >= 2]

    scored: List[Tuple[int, Product]] = []
    for p in products:
        if category and p.category != category:
            continue
        if storage and p.storage != storage:
            continue
        # Build per-product haystack with appropriate weighting
        name_hay = _norm(p.name) + " " + _norm(p.name_hi)
        cat_hay = _norm(p.category)
        kw_hay = _norm(" ".join(p.keywords))
        desc_hay = _norm(p.description)

        score_p = 0
        for t in tokens:
            # Exact substring matches (heavily weighted)
            if t in name_hay:
                score_p += 4
                continue
            if t in cat_hay:
                score_p += 2
                continue
            if t in kw_hay:
                score_p += 2
                continue
            if t in desc_hay:
                score_p += 1
                continue
            # Fuzzy fallback (typo tolerance)
            if _fuzzy_match(t, name_hay + " " + kw_hay):
                score_p += 2

        if q in name_hay:
            score_p += 5  # exact whole-query match in name

        if score_p >= MIN_PRODUCT_SCORE:
            scored.append((score_p, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:limit]]
