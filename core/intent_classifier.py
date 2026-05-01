"""Fast intent detection.

LLM classification (Groq 8B → ~100ms) with a keyword fallback that runs
before the LLM call when there's a high-confidence pattern. This means most
messages skip the LLM call entirely.
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
]

VALID: List[Intent] = [
    "ORDER", "INQUIRY", "SAMPLE", "COMPLAINT", "PAYMENT",
    "CONTACT", "LEAD", "NON_B2B", "GREETING", "GENERAL",
]


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


def _keyword_intent(message: str) -> Intent | None:
    text = message.strip()
    if not text:
        return "GREETING"
    for intent, pattern in _KW:
        if pattern.search(text):
            return intent  # type: ignore[return-value]
    return None


async def classify(message: str) -> Intent:
    """Classify a message. Tries keyword fast-path first, then LLM."""
    if not message or not message.strip():
        return "GREETING"

    kw = _keyword_intent(message)
    if kw:
        log.info("intent_keyword", extra={"intent": kw, "msg_preview": message[:60]})
        return kw

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
    # Try to find any valid intent token in the response
    for v in VALID:
        if v in raw:
            return v
    return "GENERAL"
