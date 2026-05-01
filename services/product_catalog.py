"""Product catalog: load, search, format.

Data source is `data/products.json`. Search is a deterministic keyword scorer
that handles English + Hindi + Hinglish — no LLM needed.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from config import DATA_DIR


@dataclass
class Product:
    id: str
    name: str
    name_hi: str
    category: str
    type: str  # RTE / RTC / RTS
    price_per_kg: Optional[float]
    price_per_piece: Optional[float]
    moq: int
    moq_unit: str
    pack_sizes: List[str]
    shelf_life_months: int
    storage: str  # frozen | ambient
    description: str
    use_cases: List[str]
    store_link: str
    image_url: str
    keywords: List[str]


@lru_cache
def _all() -> List[Product]:
    path = DATA_DIR / "products.json"
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return [Product(**p) for p in raw]


def _norm(text: str) -> str:
    """Normalise for matching: lowercase, strip diacritics, collapse spaces."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    only_ascii = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", only_ascii.lower()).strip()


def search(
    query: str,
    category: Optional[str] = None,
    storage: Optional[str] = None,
    limit: int = 5,
) -> List[Product]:
    """Score products by keyword match. Returns top `limit` ordered by score."""
    products = _all()
    if not query:
        # Even with no query, allow filtering
        out = products
        if category:
            out = [p for p in out if p.category == category]
        if storage:
            out = [p for p in out if p.storage == storage]
        return out[:limit]

    q = _norm(query)
    tokens = [t for t in re.split(r"[\s,;/]+", q) if len(t) >= 2]

    scored: list[tuple[int, Product]] = []
    for p in products:
        if category and p.category != category:
            continue
        if storage and p.storage != storage:
            continue
        haystacks = [
            _norm(p.name) * 3,            # name match weighted 3×
            _norm(p.name_hi) * 3,
            _norm(p.category) * 2,
            _norm(" ".join(p.keywords)),
            _norm(p.description),
        ]
        hay = " ".join(haystacks)
        score = sum(1 for t in tokens if t in hay)
        # Bonus: exact substring of full query
        if q in hay:
            score += 3
        if score > 0:
            scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:limit]]


def by_id(pid: str) -> Optional[Product]:
    for p in _all():
        if p.id == pid:
            return p
    return None


def all_products() -> List[Product]:
    return list(_all())


def categories() -> List[dict]:
    cats = {}
    for p in _all():
        if p.category not in cats:
            cats[p.category] = {"category": p.category, "count": 0, "store_link": p.store_link}
        cats[p.category]["count"] += 1
    return list(cats.values())


def to_dict(p: Product) -> dict:
    return asdict(p)
