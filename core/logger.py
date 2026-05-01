"""Structured JSON logger + error alerting.

Every log line is a single JSON object with timestamp, level, request_id,
event name, and an arbitrary `data` dict. Logs are written to stdout (so
Docker captures them) AND appended to ./storage/logs/{date}.jsonl for offline
forensics.

Also exposes:
    log_event(event, level, **fields) — the canonical log entry-point
    alert_on_error(issue, payload)    — Twilio alert with de-dupe per issue/hour
    error_burst()                     — call after each error; alerts on bursts
    bind_request_id(rid)              — context-var for the current request
"""

from __future__ import annotations

import contextvars
import json
import logging
import shutil
import sys
import time
import traceback
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict

from config import LOG_DIR, settings

# ─── Request-id propagation ──────────────────────────────────────
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "k24_request_id", default=""
)


def bind_request_id(request_id: str | None = None) -> str:
    rid = request_id or uuid.uuid4().hex[:12]
    _request_id_var.set(rid)
    return rid


def current_request_id() -> str:
    return _request_id_var.get()


# ─── JSON formatter ──────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "rid": current_request_id() or None,
            "msg": record.getMessage(),
        }
        # `extra` dict gets attached as record attributes
        for k, v in record.__dict__.items():
            if k in {
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "levelname", "levelno", "lineno", "module", "msecs",
                "msg", "name", "pathname", "process", "processName", "relativeCreated",
                "stack_info", "thread", "threadName", "message",
            }:
                continue
            try:
                json.dumps({k: v})
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        if record.exc_info:
            payload["exc"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


# ─── File handler (per-day rotation, simple) ─────────────────────
class DailyFileHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = LOG_DIR / f"{day}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            line = self.format(record) if self.formatter else record.getMessage()
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:  # noqa: BLE001
            self.handleError(record)


_setup_done = False


def setup() -> None:
    """Idempotent — call once at process start."""
    global _setup_done
    if _setup_done:
        return
    _setup_done = True

    fmt = JsonFormatter()
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    stream = logging.StreamHandler(stream=sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    file_h = DailyFileHandler()
    file_h.setFormatter(fmt)
    file_h.setLevel(logging.INFO)
    root.addHandler(file_h)

    # Suppress noisy third-party debug logs in production
    for noisy in ("uvicorn.access", "httpx", "httpcore", "urllib3", "google", "groq"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(event: str, level: str = "INFO", **fields: Any) -> None:
    """Canonical entry-point used everywhere instead of bespoke .info() calls."""
    setup()
    logger = logging.getLogger("k24")
    fn = getattr(logger, level.lower(), logger.info)
    fn(event, extra={"event": event, **fields})


# ─── Error burst tracking + alerting ─────────────────────────────
_error_window: Deque[float] = deque()
_alert_dedupe: Dict[str, float] = {}


def error_burst(error_type: str = "unknown") -> None:
    """Called from exception paths. Emits an alert if too many errors / window."""
    now = time.time()
    _error_window.append(now)
    cutoff = now - settings.ALERT_ERROR_BURST_WINDOW_S
    while _error_window and _error_window[0] < cutoff:
        _error_window.popleft()
    if len(_error_window) >= settings.ALERT_ERROR_BURST_COUNT:
        alert_on_error(
            "error_burst",
            {
                "error_type": error_type,
                "count_in_window": len(_error_window),
                "window_seconds": settings.ALERT_ERROR_BURST_WINDOW_S,
            },
        )


def alert_on_error(issue: str, payload: Dict[str, Any]) -> bool:
    """De-duped (1 per hour per issue) WhatsApp alert via Twilio."""
    now = time.time()
    last = _alert_dedupe.get(issue, 0)
    if now - last < settings.ALERT_DEDUPE_SECONDS:
        return False
    _alert_dedupe[issue] = now

    log_event("alert", level="ERROR", issue=issue, **payload)

    if not settings.ADMIN_PHONE or not settings.twilio_enabled:
        return False
    try:
        from twilio.rest import Client  # type: ignore

        body = (
            f"⚠️ K24 Chatbot Alert\n"
            f"Issue: {issue}\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M IST')}\n"
        )
        body += "\n".join(f"{k}: {v}" for k, v in payload.items())[:1200]
        Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN).messages.create(
            body=body[:1500],
            from_=settings.TWILIO_WHATSAPP_NUMBER,
            to=f"whatsapp:+{settings.ADMIN_PHONE}",
        )
        return True
    except Exception as e:  # noqa: BLE001
        log_event("alert_send_fail", level="ERROR", issue=issue, error=str(e))
        return False


def disk_check() -> int:
    """Returns free MB. Triggers alert if below the configured floor."""
    try:
        free_b = shutil.disk_usage(str(LOG_DIR)).free
    except OSError:
        return -1
    free_mb = int(free_b / (1024 * 1024))
    if 0 < free_mb < settings.ALERT_DISK_FREE_MB_FLOOR:
        alert_on_error("low_disk_space", {"free_mb": free_mb, "floor_mb": settings.ALERT_DISK_FREE_MB_FLOOR})
    return free_mb


# ─── PII helpers ─────────────────────────────────────────────────
def mask_phone(phone: str) -> str:
    """Display-safe phone number — keep first 2 and last 4 digits."""
    if not phone:
        return ""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 6:
        return "****"
    return f"{digits[:2]}{'*' * (len(digits) - 6)}{digits[-4:]}"


def hash_phone(phone: str) -> str:
    """SHA-256 of (salt + last-10-digits-of-phone). Irreversible without the salt.

    Normalised to the last 10 digits so '+91 9876 543 210' and '9876543210'
    hash to the same value.
    """
    import hashlib
    if not phone:
        return ""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not digits:
        return ""
    if len(digits) > 10:
        digits = digits[-10:]
    h = hashlib.sha256()
    h.update(settings.PII_HASH_SALT.encode("utf-8"))
    h.update(digits.encode("utf-8"))
    return h.hexdigest()
