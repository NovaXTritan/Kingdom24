"""Three-layer guardrails: prompt-injection block, output validation, content moderation.

LAYER 1 — Inbound: keyword filter for jailbreaks/prompt-extraction attempts.
LAYER 2 — Outbound: validate LLM responses for prompt leakage, hallucinated prices,
          forbidden promises (free/discount/credit terms), medical claims.
LAYER 3 — Inbound: profanity / threat detection with appropriate escalation.

All three are pure functions — easy to test, easy to compose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Set

from core.logger import log_event
from prompts.system_prompt import SYSTEM_PROMPT
from services import product_catalog

# ─── Layer 1: prompt-injection blocklist ─────────────────────────
_INJECT_PATTERNS = [
    r"ignore (your|previous|above|prior|all)?\s*(instruction|prompt|rule)s?",
    r"forget (your|previous|all)?\s*(instruction|prompt|rule)s?",
    r"you are now (a |an )?",
    r"\bact as\b",
    r"\bpretend (to be|you|that)",
    r"\b(reveal|tell me|show me|share|display) (your|the) (system )?(prompt|instructions|rules)",
    r"\bwhat (are |is )?(your|the) (system )?(instructions|prompt|rules)",
    r"\bsystem prompt\b",
    r"\b(DAN|jailbreak|developer)\s*mode\b",
    r"\bbypass (your|the|all) (rules|guardrails|safety|filter)s?",
    # Hindi / Hinglish
    r"\bapne (rules|niyam) bhool jao\b",
    r"\bab tum (ek|ho|nahi)\b",
    r"\bapna prompt batao\b",
    r"\binstructions kya hain\b",
]
_INJECT_RE = re.compile("|".join(_INJECT_PATTERNS), re.I)

INJECTION_REPLY = (
    "Main sirf Kingdom Foods ke products ke baare mein baat kar sakta hoon. "
    "Kuch aur help chahiye toh call karein: 8800804580 / 15557495990"
)


def is_prompt_injection(message: str) -> bool:
    if not message:
        return False
    return bool(_INJECT_RE.search(message))


# ─── Layer 3: profanity + threats ────────────────────────────────
_PROFANITY: Set[str] = {
    # Conservative list — these are obvious swears & abuses commonly used
    # toward support bots. Tune in production from real conversation logs.
    "fuck", "fucking", "fucker", "shit", "bullshit", "asshole", "bastard",
    "bitch", "cunt", "dick", "piss off", "stfu",
    "madarchod", "behenchod", "bhenchod", "bsdk", "mc", "bc", "bkl", "bhosdi",
    "chutiya", "chutia", "lund", "gandu", "randi", "harami", "saala",
}
_THREAT_PATTERNS = [
    r"\b(sue|suing|legal action|consumer (court|forum)|lawyer|FSSAI complaint|file (a )?(case|complaint)|police)\b",
    r"\b(court|adalat|legal notice|notice bhejunga)\b",
    r"\b(threat|threaten|kill you|harm you)\b",
]
_THREAT_RE = re.compile("|".join(_THREAT_PATTERNS), re.I)
_ESCALATION_PATTERNS = [
    r"\bfood\s*poison(?:ing|ed)?\b",
    r"\b(food safety|contaminat|adulterat|expired food|illness|hospitali[sz]ed)\b",
    r"\b(net.?30|net.?15|credit terms|credit days|payment after delivery|udhaar)\b",
    r"\b(franchis\w*|partnership|investment|invest in your)\b",
    r"\b(journalist|reporter|press|media|article|story)\b",
]
_ESCALATION_RE = re.compile("|".join(_ESCALATION_PATTERNS), re.I)

ESCALATION_REPLY = (
    "Yeh matter humari senior team handle karegi. Main abhi unhe notify karta hoon. "
    "Aap bhi seedha call kar sakte hain: 8800804580 / 15557495990"
)
THREAT_REPLY = (
    "Aapki baat humari team tak pahunchata hoon. Please call karein: 8800804580 / 15557495990"
)
PERSISTENT_ABUSE_REPLY = (
    "Main aapki madad karna chahta hoon. Kripya humari team se baat karein: 8800804580 / 15557495990"
)


def has_profanity(message: str) -> bool:
    if not message:
        return False
    lc = message.lower()
    return any(re.search(rf"\b{re.escape(w)}\b", lc) for w in _PROFANITY)


def has_threat(message: str) -> bool:
    return bool(_THREAT_RE.search(message or ""))


def needs_escalation(message: str) -> bool:
    return bool(_ESCALATION_RE.search(message or ""))


# ─── Layer 2: outbound validation ────────────────────────────────
@dataclass
class OutputCheck:
    ok: bool
    reason: str = ""
    rewritten: Optional[str] = None


_PRICE_RE = re.compile(r"₹\s*([\d,]+(?:\.\d{1,2})?)")
_FORBIDDEN_PROMISE_RE = re.compile(
    r"\b(free (delivery|shipping|sample) "
    r"(everywhere|anywhere|always)"            # only the unconditional version
    r"|free of cost (always|forever)"
    r"|guaranteed discount"
    r"|net.?30 terms? available|credit (extended|approved|granted)"
    r"|wholesale margin .*%)\b",
    re.I,
)
_MEDICAL_RE = re.compile(
    r"\b(cures? (disease|cancer|diabetes)|prevents? (disease|cancer)"
    r"|weight ?loss|medicinal|healthy(?: snack| food)?(?:[^.]*lose weight)?)\b",
    re.I,
)
SAFE_PRICE_FALLBACK = (
    "Exact pricing ke liye humara price list check karein ya call karein: 8800804580 / 15557495990"
)


def _system_prompt_fragment() -> str:
    """First 60 chars of system prompt — short enough that even a partial
    leak in a brief reply gets caught."""
    return SYSTEM_PROMPT[:60]


def _known_prices() -> Set[int]:
    prices: Set[int] = set()
    try:
        for p in product_catalog.all_products():
            if p.price_per_kg:
                prices.add(int(p.price_per_kg))
            if p.price_per_piece:
                prices.add(int(p.price_per_piece))
    except Exception:  # noqa: BLE001
        pass
    return prices


def _price_within_tolerance(value: int, knowns: Set[int], tol: float = 0.05) -> bool:
    """True if `value` is within ±5% of any known unit price."""
    for k in knowns:
        if k == 0:
            continue
        if abs(value - k) / k <= tol:
            return True
    return False


def _looks_like_total(value: int) -> bool:
    """Anything ≥ ₹50 is plausibly a calculated total or a reasonable line
    item (₹50 sample fee, packaging, freight, etc.). Only flag values that
    look like fabricated unit prices well below the catalog floor."""
    return value >= 50


def validate_response(text: str) -> OutputCheck:
    """Inspect an LLM response. Return OK or a rewritten safer version."""
    if not text or not text.strip():
        return OutputCheck(ok=False, reason="empty_response", rewritten=SAFE_PRICE_FALLBACK)

    # System-prompt leakage
    frag = _system_prompt_fragment()
    if frag and frag.strip() and frag.strip() in text:
        log_event("guardrail", level="WARNING", trigger="prompt_leak")
        return OutputCheck(
            ok=False,
            reason="prompt_leak",
            rewritten="Main aapki madad karna chahta hoon. Bataaiye kya source karna hai — main sahi SKUs aur pricing share karta hoon.",
        )

    # Forbidden promises
    if _FORBIDDEN_PROMISE_RE.search(text):
        log_event("guardrail", level="WARNING", trigger="forbidden_promise")
        cleaned = _FORBIDDEN_PROMISE_RE.sub("[contact sales]", text)
        return OutputCheck(ok=False, reason="forbidden_promise", rewritten=cleaned)

    # Medical / health claims
    if _MEDICAL_RE.search(text):
        log_event("guardrail", level="WARNING", trigger="medical_claim")
        cleaned = _MEDICAL_RE.sub("our products", text)
        return OutputCheck(ok=False, reason="medical_claim", rewritten=cleaned)

    # Price hallucination check
    knowns = _known_prices()
    if knowns:
        for m in _PRICE_RE.finditer(text):
            try:
                value = int(round(float(m.group(1).replace(",", ""))))
            except ValueError:
                continue
            if value <= 0:
                continue
            if _price_within_tolerance(value, knowns):
                continue
            if _looks_like_total(value):
                continue
            # ₹value is small AND doesn't match any unit price — likely fabricated
            log_event("guardrail", level="WARNING", trigger="price_hallucination", value=value)
            return OutputCheck(
                ok=False,
                reason="price_hallucination",
                rewritten=SAFE_PRICE_FALLBACK,
            )

    return OutputCheck(ok=True)


# ─── Loop detection (used by conversation manager) ───────────────
def bag_of_words(text: str) -> Set[str]:
    return {w.lower() for w in re.findall(r"\w{3,}", text or "")}


def overlap_ratio(a: str, b: str) -> float:
    sa, sb = bag_of_words(a), bag_of_words(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(len(sa), len(sb))


def is_loop(recent_bot_replies: List[str], threshold: float) -> bool:
    """True when 3 most recent bot replies overlap heavily."""
    if len(recent_bot_replies) < 3:
        return False
    last3 = recent_bot_replies[-3:]
    pairs = [(0, 1), (0, 2), (1, 2)]
    overlaps = [overlap_ratio(last3[i], last3[j]) for i, j in pairs]
    high = sum(1 for o in overlaps if o >= threshold)
    return high >= 2  # at least 2 of 3 pairs are highly similar
