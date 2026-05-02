"""Extract structured lead data from free-form conversation messages.

Two-stage strategy:
  1. Regex pass — fast and free, catches phone numbers, cities, volumes, keywords
  2. LLM pass — only for messages > 20 chars where regex didn't fill a key field

Calculates lead score (0-100) with weights from `config.lead_score_weights`,
applies time-based decay on read, supports duplicate-lead merge by phone.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Dict, Optional

from config import settings
from core.conversation import Conversation
from core.llm_router import Message, generate

log = logging.getLogger("k24.lead")

# ─── Regex extractors ────────────────────────────────────────────
_PHONE_CANDIDATE = re.compile(r"(?<!\d)([6-9]\d{9})(?!\d)")
_PRICE_CONTEXT_RE = re.compile(r"(₹|rs\.?|rupee|inr)\s*[\d,]*$", re.I)
_HSN_CONTEXT_RE = re.compile(r"\bHSN\s*[:\-]?\s*$", re.I)
_QTY_CONTEXT_RE = re.compile(r"\b(pieces?|pcs|kg|kilo|grams?|gms?|ton|tonne)\b", re.I)
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

# Person-name extraction. Match common introduction patterns. The skip-list
# rejects English words that happen to be capitalised at sentence start.
_NAME_PATTERNS = [
    re.compile(r"(?:I'm|I am|my name is|this is|mera naam|naam hai)\s+([A-Z][a-z]{1,15})", re.I),
    re.compile(r"^([A-Z][a-z]{1,15})\s+(?:here|hai|hoon|speaking|from)\b"),
]
_NAME_SKIP = {
    "I", "Im", "My", "The", "This", "Just", "Looking", "Need", "Want", "Help", "Please",
    "Yes", "No", "Ok", "Okay", "Hi", "Hey", "Hello", "Good", "Fine", "Thanks", "Sure",
    "We", "Us", "Our", "Ours", "Mine", "Your", "Their", "Quote", "Show", "Tell", "Send",
}


def extract_name(message: str) -> Optional[str]:
    """Extract a person's first name from an introduction. Returns None if absent."""
    for pattern in _NAME_PATTERNS:
        m = pattern.search(message)
        if m:
            name = m.group(1).strip()
            # Title-case for storage; reject if it's a common English starter word
            if name.title() in _NAME_SKIP:
                continue
            return name.title()
    return None

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


def _looks_like_phone(message: str, candidate: str, span_start: int) -> bool:
    """Disambiguate a 10-digit number — is it a phone or a price/HSN/quantity?"""
    # Look at characters preceding the candidate (up to 30 back)
    pre_window = message[max(0, span_start - 30): span_start]
    if _PRICE_CONTEXT_RE.search(pre_window):
        return False
    if _HSN_CONTEXT_RE.search(pre_window):
        return False
    # Look at the next 8 chars after candidate — if they're a quantity unit, it's qty
    end = span_start + len(candidate)
    post_window = message[end: end + 8]
    if post_window and _QTY_CONTEXT_RE.match(post_window.lstrip()):
        return False
    # Mobile prefix sanity (already enforced by candidate regex starting 6-9, but
    # extra check for super-common misuse like "1000000000" — that's not phone-ish)
    if candidate.startswith(("60", "61", "62", "63", "64", "65")):
        # Allowed prefixes per TRAI: 6-9. Reject low ones that don't look like real Indian mobiles.
        # In practice TRAI assigns from 60-69 but conservatively accept 6X, then 7-9.
        pass
    return True


# ─── Main extractor ──────────────────────────────────────────────
async def extract_and_update(conv: Conversation, message: str) -> Dict[str, str]:
    """Update conv.lead_data in-place with what's discoverable in `message`.

    Returns the diff (new keys / overwrites) for logging.
    """
    diff: Dict[str, str] = {}

    for m in _PHONE_CANDIDATE.finditer(message):
        candidate = m.group(1)
        if not _looks_like_phone(message, candidate, m.start()):
            continue
        if conv.lead_data.get("phone") != candidate:
            conv.lead_data["phone"] = candidate
            diff["phone"] = candidate
        break  # first valid phone wins

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

    if (name := extract_name(message)) and conv.lead_data.get("contact_name") != name:
        conv.lead_data["contact_name"] = name
        conv.customer_name = name
        diff["contact_name"] = name

    # LLM extraction — only if message is substantial AND we still need outlet_name
    if len(message) > 20 and not conv.lead_data.get("outlet_name"):
        outlet = await _llm_extract_outlet(message)
        if outlet:
            conv.lead_data["outlet_name"] = outlet
            diff["outlet_name"] = outlet

    conv.lead_score = score(conv.lead_data, conv.created_at)
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


# ─── Score (with optional decay) ─────────────────────────────────
def score(lead: Dict[str, str], created_at: Optional[float] = None) -> int:
    base = sum(weight for key, weight in settings.lead_score_weights.items() if lead.get(key))
    if not created_at:
        return base
    age_days = (time.time() - created_at) / 86400
    if age_days >= settings.LEAD_COLD_DAYS:
        return int(base * settings.LEAD_DECAY_30D * 0.6)
    if age_days >= 30:
        return int(base * settings.LEAD_DECAY_30D)
    if age_days >= 7:
        return int(base * settings.LEAD_DECAY_7D)
    return base


def missing_fields(lead: Dict[str, str]) -> list[str]:
    return [k for k in settings.lead_score_weights if not lead.get(k)]


def is_cold(created_at: float) -> bool:
    return (time.time() - created_at) / 86400 >= settings.LEAD_COLD_DAYS


# ─── Backwards-compat shim for existing imports ──────────────────
WEIGHTS = settings.lead_score_weights
