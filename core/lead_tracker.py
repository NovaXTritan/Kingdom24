"""Extract structured lead data from free-form conversation messages.

Two-stage strategy:
  1. Regex pass — fast and free, catches phone numbers, cities, volumes, keywords
  2. LLM pass — only for messages > 20 chars where regex didn't fill a key field

Calculates lead score (0-100) and triggers the sales-team alert at threshold.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Optional

from config import settings
from core.conversation import Conversation
from core.llm_router import Message, generate

log = logging.getLogger("k24.lead")

# ─── Regex extractors ────────────────────────────────────────────
_PHONE = re.compile(r"\b([6-9]\d{9})\b")
_VOLUME = re.compile(
    r"(\d+(?:\.\d+)?)\s*(kg|kilo|kilogram|ton|tonne|tonnes|piece|pieces|pcs|box|boxes|carton|cartons)",
    re.I,
)
_BUSINESS = {
    "hotel": ["hotel", "five star", "5 star"],
    "restaurant": ["restaurant", "resto", "fine dine", "dine in", "dhaba", "cafe", "café"],
    "cloud_kitchen": ["cloud kitchen", "ghost kitchen", "dark kitchen"],
    "caterer": ["caterer", "catering", "banquet", "wedding catering"],
    "qsr": ["qsr", "quick service", "fast food", "takeaway", "take away"],
    "distributor": ["distributor", "wholesaler", "trader", "stockist"],
    "institutional": ["institutional", "canteen", "mess", "hostel"],
}
_STORAGE = {
    "frozen": ["frozen", "freezer", "-18", "blast", "iqf", "deep freeze"],
    "ambient": ["ambient", "room temp", "shelf stable", "non frozen", "non-frozen"],
    "chilled": ["chilled", "chiller", "refrigerated", "0-4", "fridge"],
}
_DECISION = re.compile(r"\b(owner|founder|director|chef|head chef|gm|f&b|procurement|partner)\b", re.I)
_EMAIL = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")

# Top-50 Indian cities (lowercase, used for substring search)
_CITIES = {
    "delhi", "new delhi", "noida", "gurugram", "gurgaon", "ghaziabad", "faridabad",
    "greater noida", "meerut", "mumbai", "thane", "navi mumbai", "pune", "nagpur",
    "nashik", "bangalore", "bengaluru", "mysore", "hyderabad", "secunderabad",
    "chennai", "coimbatore", "madurai", "kolkata", "howrah", "ahmedabad", "surat",
    "vadodara", "rajkot", "jaipur", "udaipur", "jodhpur", "lucknow", "kanpur",
    "varanasi", "patna", "ranchi", "bhubaneswar", "raipur", "indore", "bhopal",
    "gwalior", "chandigarh", "ludhiana", "amritsar", "jalandhar", "dehradun",
    "haridwar", "shimla", "guwahati", "kochi", "ernakulam", "trivandrum", "calicut",
}


def _detect_business_type(text: str) -> Optional[str]:
    t = text.lower()
    for canonical, kws in _BUSINESS.items():
        if any(kw in t for kw in kws):
            return canonical
    return None


def _detect_storage(text: str) -> Optional[str]:
    t = text.lower()
    for canonical, kws in _STORAGE.items():
        if any(kw in t for kw in kws):
            return canonical
    return None


def _detect_city(text: str) -> Optional[str]:
    t = text.lower()
    for city in _CITIES:
        # Word-boundary substring (cheap, avoids false positives like "no" in "noida")
        if re.search(rf"\b{re.escape(city)}\b", t):
            return city.title()
    return None


def _normalize_volume(value: str, unit: str) -> str:
    u = unit.lower()
    n = float(value)
    if u in {"ton", "tonne", "tonnes"}:
        n *= 1000
        u = "kg"
    if u in {"kilo", "kilogram"}:
        u = "kg"
    if u in {"piece", "pieces"}:
        u = "pcs"
    return f"{int(n) if n.is_integer() else n} {u}"


# ─── Main extractor ──────────────────────────────────────────────
async def extract_and_update(conv: Conversation, message: str) -> Dict[str, str]:
    """Update conv.lead_data in-place with what's discoverable in `message`.

    Returns the diff (new keys / overwrites) for logging.
    """
    diff: Dict[str, str] = {}

    if m := _PHONE.search(message):
        if conv.lead_data.get("phone") != m.group(1):
            conv.lead_data["phone"] = m.group(1)
            diff["phone"] = m.group(1)

    if m := _EMAIL.search(message):
        if conv.lead_data.get("email") != m.group(0):
            conv.lead_data["email"] = m.group(0)
            diff["email"] = m.group(0)

    if (city := _detect_city(message)) and conv.lead_data.get("city") != city:
        conv.lead_data["city"] = city
        diff["city"] = city

    if (bt := _detect_business_type(message)) and conv.lead_data.get("business_type") != bt:
        conv.lead_data["business_type"] = bt
        diff["business_type"] = bt

    if (st := _detect_storage(message)) and conv.lead_data.get("storage") != st:
        conv.lead_data["storage"] = st
        diff["storage"] = st

    if m := _VOLUME.search(message):
        vol = _normalize_volume(m.group(1), m.group(2))
        if conv.lead_data.get("volume") != vol:
            conv.lead_data["volume"] = vol
            diff["volume"] = vol

    if _DECISION.search(message) and not conv.lead_data.get("decision_maker"):
        conv.lead_data["decision_maker"] = "true"
        diff["decision_maker"] = "true"

    # Light LLM extraction: only if message is substantial AND we still need outlet_name
    if len(message) > 20 and not conv.lead_data.get("outlet_name"):
        outlet = await _llm_extract_outlet(message)
        if outlet:
            conv.lead_data["outlet_name"] = outlet
            diff["outlet_name"] = outlet

    conv.lead_score = score(conv.lead_data)
    return diff


async def _llm_extract_outlet(message: str) -> Optional[str]:
    """Try to pull an outlet/business name out of free text."""
    prompt = (
        "Extract the outlet name / business name from this message if mentioned. "
        "Return ONLY the name, or 'NONE' if no business name is mentioned. "
        f"Message: {message!r}"
    )
    res = await generate(
        [Message(role="user", content=prompt)],
        task_type="classification",
        temperature=0.0,
    )
    if res.error or not res.text:
        return None
    txt = res.text.strip().strip("'\"`")
    if not txt or txt.upper() in {"NONE", "N/A", "NULL"} or len(txt) > 80:
        return None
    return txt


# ─── Score ───────────────────────────────────────────────────────
WEIGHTS = {
    "business_type": 15,
    "outlet_name": 15,
    "city": 10,
    "phone": 20,
    "volume": 15,
    "storage": 10,
    "decision_maker": 15,
}


def score(lead: Dict[str, str]) -> int:
    return sum(weight for key, weight in WEIGHTS.items() if lead.get(key))


def missing_fields(lead: Dict[str, str]) -> list[str]:
    return [k for k in WEIGHTS if not lead.get(k)]
