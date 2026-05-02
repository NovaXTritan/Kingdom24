"""Fast intent + language detection.

Three-stage pipeline per message:
  1. detect_language() — ENGLISH / HINDI / HINGLISH (deterministic, no LLM)
  2. is_food_or_business_message() — relevance gate. Off-topic short-circuits
     the rest of the pipeline so we never recommend momos to "help me with
     time management" type messages.
  3. classify() — keyword fast-path → Groq 8B LLM fallback.
"""

from __future__ import annotations

import logging
import re
from typing import List, Literal

from core.llm_router import Message, generate
from prompts.intent_prompt import INTENT_PROMPT

log = logging.getLogger("k24.intent")

Intent = Literal[
    "ORDER",
    "INQUIRY",
    "SAMPLE",
    "COMPLAINT",
    "PAYMENT",
    "CONTACT",
    "LEAD",
    "NON_B2B",
    "GREETING",
    "GENERAL",
    "OFF_TOPIC",
]

VALID: List[Intent] = [
    "ORDER", "INQUIRY", "SAMPLE", "COMPLAINT", "PAYMENT",
    "CONTACT", "LEAD", "NON_B2B", "GREETING", "GENERAL", "OFF_TOPIC",
]


# ── Language detection ───────────────────────────────────────────
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")

_HINDI_WORDS = frozenset({
    'hai', 'hain', 'ka', 'ki', 'ke', 'ko', 'se', 'mein', 'pe', 'hoon', 'ho',
    'kya', 'kaise', 'kab', 'kahan', 'kyun', 'aap', 'tum', 'hum', 'woh', 'yeh',
    'chahiye', 'karo', 'karna', 'bhai', 'yaar', 'ji', 'nahi', 'mat', 'abhi',
    'bahut', 'achha', 'batao', 'bhejo', 'dedo', 'kitna', 'aur', 'lekin', 'toh',
    'bhi', 'maal', 'saman', 'daam', 'khana', 'dukan', 'dhaba', 'mangta',
    'lena', 'dena', 'dikha', 'padh', 'bol', 'sun', 'chal', 'ruk',
    'mera', 'meri', 'tera', 'teri', 'hamara', 'aapka', 'apka',
})


def detect_language(message: str) -> str:
    """Return ENGLISH, HINDI, or HINGLISH for the given user message.

    Devanagari script anywhere → HINDI.
    Otherwise: count Hindi-marker words (romanized). ≥25% of tokens → HINGLISH,
    else ENGLISH.
    """
    if not message or not message.strip():
        return "ENGLISH"
    if _DEVANAGARI_RE.search(message):
        return "HINDI"

    words = [w.lower().strip(".,!?:;'\"()") for w in message.split()]
    words = [w for w in words if w]
    if not words:
        return "ENGLISH"

    hindi_count = sum(1 for w in words if w in _HINDI_WORDS)
    ratio = hindi_count / len(words)
    if ratio >= 0.25:
        return "HINGLISH"
    return "ENGLISH"


# ── Off-topic / food-relevance detection ─────────────────────────
FOOD_BUSINESS_WORDS = frozenset({
    # Products
    'food', 'frozen', 'momos', 'momo', 'samosa', 'paratha', 'parantha',
    'gravy', 'curry', 'bread', 'naan', 'roti', 'snack', 'snacks',
    'vegetable', 'vegetables', 'paneer', 'chicken', 'mutton', 'lamb',
    'peas', 'corn', 'onion', 'tomato', 'ginger', 'garlic', 'masala', 'spice',
    'pickle', 'chutney', 'biryani', 'pulao', 'rice', 'dal', 'rajma', 'chole',
    'tikki', 'cutlet', 'croissant', 'puff', 'kulcha', 'kebab', 'kabab',
    'tikka', 'sambar', 'idli', 'dosa', 'uttapam', 'vada', 'chaap',
    'noodle', 'noodles', 'manchurian', 'schezwan', 'hakka',
    'gulab', 'jamun', 'rasgulla', 'rasmalai', 'kheer', 'halwa', 'jalebi',
    'mousse', 'cake', 'pastry', 'brownie',
    # Business
    'restaurant', 'restaurants', 'hotel', 'hotels', 'kitchen', 'kitchens',
    'caterer', 'caterers', 'catering', 'qsr', 'cafe', 'café',
    'distributor', 'supplier', 'suppliers', 'wholesale', 'bulk',
    'order', 'orders', 'price', 'prices', 'pricing', 'rate', 'rates',
    'delivery', 'sample', 'samples', 'catalog', 'catalogue', 'product',
    'products', 'menu', 'stock', 'inventory',
    'b2b', 'supply', 'vendor', 'quote', 'quotation', 'invoice', 'horeca',
    'banquet', 'event',
    # Food industry
    'ambient', 'rte', 'rtc', 'rts', 'fssai', 'iso', 'freezer',
    'shelf', 'expiry', 'packaging', 'plate', 'meal', 'serving',
    # Units (qty cues)
    'kg', 'kilo', 'kilogram', 'ton', 'tonne', 'piece', 'pieces', 'pcs',
    'pack', 'packs', 'carton', 'box', 'boxes',
    # Hindi food/business terms (romanized)
    'maal', 'saman', 'daam', 'khana', 'khaana', 'dukan', 'dhaba', 'rasoi',
    'sabzi', 'chawal', 'sample',
})

_FOOD_BIZ_PHRASES = (
    'cold storage', 'cold chain', 'shelf life', 'spring roll',
    'cloud kitchen', 'dark kitchen', 'ghost kitchen', 'food service',
    'food business', 'frozen food', 'ready to eat', 'ready to cook',
    'food supply', 'food industry',
)

_QTY_PATTERN = re.compile(r'\d+\s*(kg|kilo|ton|tonne|piece|pcs|pack|box|carton)', re.I)
_PRICE_PATTERN = re.compile(r'(₹|rs\.?|rupee|inr|price|rate|cost)\s*\d', re.I)


def is_food_or_business_message(message: str) -> bool:
    """Does this message mention food, B2B kitchen ops, quantities, or prices?

    Used as a relevance gate — messages that fail this AND are >3 words long
    get classified as OFF_TOPIC immediately, preventing the LLM from
    hallucinating product recommendations for unrelated queries.
    """
    if not message:
        return False
    lower = message.lower()
    words = set(re.split(r"[\s,.\-!?;:()'\"]+", lower))
    if words & FOOD_BUSINESS_WORDS:
        return True
    for phrase in _FOOD_BIZ_PHRASES:
        if phrase in lower:
            return True
    if _QTY_PATTERN.search(lower):
        return True
    if _PRICE_PATTERN.search(lower):
        return True
    return False


# ── Keyword shortcuts (case-insensitive, run before LLM) ──────────
_KW = [
    ("GREETING", re.compile(r"^\s*(hi|hello|hey|namaste|namaskar|good (morning|afternoon|evening)|salaam)\b", re.I)),
    ("CONTACT", re.compile(r"\b(phone|number|address|location|whatsapp|wa\.me|google maps|kaha hain|pata)\b", re.I)),
    ("SAMPLE", re.compile(r"\b(sample|trial|trial pack|free pack|chakhna|swaad)\b", re.I)),
    ("PAYMENT", re.compile(r"\b(payment|pay now|razorpay|invoice|bill|paisa|kaise pay)\b", re.I)),
    ("COMPLAINT", re.compile(r"\b(complain|issue|problem|wastage|kharab|defective|missing|delay|late|refund)\b", re.I)),
    ("NON_B2B", re.compile(r"\b(home|family|personal use|ghar(?: ke liye| par)?|house party|small order|gift|individual)\b", re.I)),
    ("ORDER", re.compile(r"\b(order|buy|purchase|book|place an order|kharidna|order karna)\b", re.I)),
    ("LEAD", re.compile(r"\b(introduce|partnership|distributor|new business|setup|wholesale registration)\b", re.I)),
    ("INQUIRY", re.compile(r"\b(prices?|pricing|costs?|rates?|catalog|catalogue|menu|products?|range|available|stock|kya hai|hain kya|kitna hai)\b", re.I)),
]


_SHORT_PASSTHROUGH = frozenset({
    "hi", "hey", "hello", "yes", "no", "ok", "okay", "ya", "yeah",
    "haan", "nahi", "thanks", "thank you", "tnx",
})


def _keyword_intent(message: str) -> Intent | None:
    text = message.strip()
    if not text:
        return "GREETING"
    for intent, pattern in _KW:
        if pattern.search(text):
            return intent  # type: ignore[return-value]
    return None


async def classify(message: str) -> Intent:
    """Classify a message. Tries keyword fast-path first, then LLM.

    OFF_TOPIC short-circuit applies BEFORE keyword matching: if the message
    is clearly unrelated to food/business AND is more than ~3 words, it gets
    OFF_TOPIC regardless of any keyword match. (Short follow-ups like "yes"
    bypass this.)
    """
    if not message or not message.strip():
        return "GREETING"

    clean = message.strip()
    lower = clean.lower()

    # Short messages: skip everything, let keyword decide
    if len(clean) <= 3 or lower in _SHORT_PASSTHROUGH:
        return _keyword_intent(clean) or "GREETING"

    # Keyword fast-path FIRST — high-confidence patterns (CONTACT, PAYMENT,
    # NON_B2B etc.) win over the off-topic gate. Asking for "phone number"
    # is engaging with the business even if no food keyword is present.
    kw = _keyword_intent(message)
    if kw:
        log.info("intent_keyword", extra={"intent": kw, "msg_preview": message[:60]})
        return kw

    # Off-topic gate — runs AFTER keyword. Catches messages that didn't match
    # any keyword AND have no food/business relevance. The >3-word floor
    # avoids gating short phrases.
    if (
        clean != "__greeting__"
        and len(clean.split()) > 3
        and not is_food_or_business_message(clean)
    ):
        log.info("intent_off_topic", extra={"msg_preview": clean[:60]})
        return "OFF_TOPIC"

    # LLM fallback
    prompt = INTENT_PROMPT.format(message=message[:500])
    result = await generate(
        [Message(role="user", content=prompt)],
        task_type="classification",
        temperature=0.0,
    )
    if result.error:
        log.warning("intent_llm_fail", extra={"error": result.error})
        return "GENERAL"
    raw = result.text.strip().upper().split()[0].rstrip(".,:;")
    if raw in VALID:
        return raw  # type: ignore[return-value]
    for v in VALID:
        if v in raw:
            return v
    return "GENERAL"
