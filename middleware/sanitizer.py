"""Input sanitisation — applied to every user-facing endpoint.

Pure functions so they're trivial to unit-test. The integration shim lives
in ``main.py`` and ``channels/website.py``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Optional

from config import settings

# ─── Allow-listed page-url prefixes ──────────────────────────────
_ALLOWED_PAGE_URL_PREFIXES = (
    "https://kingdom24.in",
    "https://www.kingdom24.in",
    "http://localhost",
    "/",        # relative paths from same-origin embed
    "whatsapp", # synthetic value used by Twilio channel
)

# ─── Patterns ────────────────────────────────────────────────────
_HTML_TAG_RE = re.compile(
    r"<\s*(script|img|iframe|style|link|svg|object|embed|meta|form|input|video|audio)\b[^>]*>.*?(?:</\s*\1\s*>|$)",
    re.I | re.S,
)
# Stripped lone tags too (e.g. `<img>` self-closing, or `<br>`)
_LONE_TAG_RE = re.compile(r"<\s*/?\s*(script|img|iframe|style|link|svg|object|embed|meta|form|input|video|audio)\b[^>]*>", re.I)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")
_WS_RE = re.compile(r"[ \t]{2,}")
_NL_RE = re.compile(r"\n{3,}")

_TRUNCATED_NOTICE = "\n…[truncated]"


@dataclass
class SanitizedMessage:
    text: str
    truncated: bool
    original_length: int
    notice: Optional[str] = None


def sanitize_message(raw: str) -> SanitizedMessage:
    if raw is None:
        return SanitizedMessage(text="", truncated=False, original_length=0)
    s = str(raw)
    original_length = len(s)

    # 1. Strip dangerous HTML
    s = _HTML_TAG_RE.sub("", s)
    s = _LONE_TAG_RE.sub("", s)

    # 2. Strip control characters (keep \n, \t, \r)
    s = _CONTROL_RE.sub("", s)

    # 3. Collapse whitespace
    s = _WS_RE.sub(" ", s)
    s = _NL_RE.sub("\n\n", s)
    s = s.strip()

    # 4. Truncate
    truncated = False
    notice = None
    if len(s) > settings.MAX_MESSAGE_LENGTH:
        s = s[: settings.MAX_MESSAGE_LENGTH - len(_TRUNCATED_NOTICE)] + _TRUNCATED_NOTICE
        truncated = True
        notice = (
            f"Message bahut lamba hai, pehle {settings.MAX_MESSAGE_LENGTH} "
            "characters process kar rahe hain."
        )

    return SanitizedMessage(
        text=s,
        truncated=truncated,
        original_length=original_length,
        notice=notice,
    )


def is_valid_uuid_or_id(value: str) -> bool:
    """Accept UUID4 OR our ``cv_<hex>`` IDs OR Twilio channel ID ``wa_<phone>``."""
    if not value:
        return True   # blank → server will mint a fresh one
    if value.startswith(("cv_", "wa_")):
        return bool(re.match(r"^(cv_|wa_)[A-Za-z0-9_\-]{1,40}$", value))
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def is_allowed_page_url(value: str) -> bool:
    if not value:
        return True   # synthetic — fine
    v = value.strip().lower()
    return any(v.startswith(p) for p in _ALLOWED_PAGE_URL_PREFIXES)


def safe_page_url(value: str) -> str:
    return value if is_allowed_page_url(value) else "/"


def is_oversized(content_length: Optional[int]) -> bool:
    if not content_length:
        return False
    try:
        return int(content_length) > settings.MAX_REQUEST_BYTES
    except (TypeError, ValueError):
        return False
