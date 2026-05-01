"""Multi-LLM router with cascading failover, circuit breaker, persistent
quota counter, response validation and per-call timeouts.

Cascades by task type:
    customer_facing → Gemini Flash → Groq 70B → Ollama → deterministic
    classification  → Groq 8B      → Gemini   → Ollama → keyword fallback
    reasoning       → Groq 70B     → Gemini   → Ollama → deterministic
    batch           → Ollama only

Returns a `LLMResult` with the model name actually used and latency, so
upstream code can attribute responses for analytics. Circuit-breaker state
is per-provider; quota counters are persisted to disk so restarts honour them.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import List, Literal, Optional, Tuple

import httpx

from config import STORAGE_DIR, settings
from core.logger import alert_on_error, log_event

TaskType = Literal["customer_facing", "classification", "reasoning", "batch"]
Role = Literal["system", "user", "assistant"]


@dataclass
class Message:
    role: Role
    content: str


@dataclass
class LLMResult:
    text: str
    model: str
    latency_ms: int
    error: Optional[str] = None


# ─── Persistent rate counter (file-backed, atomic) ───────────────
_USAGE_FILE = STORAGE_DIR / "llm_usage.json"
_usage_lock = threading.Lock()


def _today_iso() -> str:
    return date.today().isoformat()


def _load_usage() -> dict:
    if not _USAGE_FILE.exists():
        return {}
    try:
        with _USAGE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _atomic_write(payload: dict) -> None:
    tmp = _USAGE_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(_USAGE_FILE)


def _read_today_counter(name: str) -> int:
    with _usage_lock:
        u = _load_usage()
        e = u.get(name, {})
        if e.get("date") != _today_iso():
            return 0
        return int(e.get("count", 0))


def _increment_today_counter(name: str) -> int:
    with _usage_lock:
        u = _load_usage()
        e = u.get(name, {})
        if e.get("date") != _today_iso():
            e = {"date": _today_iso(), "count": 0}
        e["count"] = int(e.get("count", 0)) + 1
        u[name] = e
        _atomic_write(u)
        return int(e["count"])


def gemini_used_today() -> int:
    return _read_today_counter("gemini")


def gemini_remaining_today() -> int:
    return max(0, settings.GEMINI_DAILY_LIMIT - gemini_used_today())


# ─── In-process Groq RPM tracker ─────────────────────────────────
_groq_lock = threading.Lock()
_groq_minute = int(time.time() // 60)
_groq_count = 0


def _groq_check_and_increment() -> Tuple[bool, int]:
    global _groq_minute, _groq_count
    m = int(time.time() // 60)
    with _groq_lock:
        if m != _groq_minute:
            _groq_minute = m
            _groq_count = 0
        if _groq_count >= settings.GROQ_RPM_HARD_STOP:
            return False, _groq_count
        _groq_count += 1
        return True, _groq_count


def groq_rpm_current() -> int:
    return _groq_count


# ─── Circuit breaker ────────────────────────────────────────────
class CBState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-provider trip switch. Thread-safe via a single lock."""

    def __init__(self, name: str, failure_threshold: int, open_seconds: int) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds
        self._lock = threading.Lock()
        self._state: CBState = CBState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float = 0.0
        self._half_open_in_flight = False

    @property
    def state(self) -> CBState:
        with self._lock:
            if self._state == CBState.OPEN and time.time() - self._opened_at >= self.open_seconds:
                self._state = CBState.HALF_OPEN
                self._half_open_in_flight = False
                log_event("circuit", level="INFO", provider=self.name, transition="open->half_open")
            return self._state

    def allow(self) -> bool:
        with self._lock:
            st = self._state
            if st == CBState.CLOSED:
                return True
            if st == CBState.OPEN:
                if time.time() - self._opened_at >= self.open_seconds:
                    self._state = CBState.HALF_OPEN
                    self._half_open_in_flight = True
                    log_event("circuit", level="INFO", provider=self.name, transition="open->half_open")
                    return True
                return False
            # HALF_OPEN
            if self._half_open_in_flight:
                return False
            self._half_open_in_flight = True
            return True

    def record_success(self) -> None:
        with self._lock:
            if self._state != CBState.CLOSED:
                log_event("circuit", level="INFO", provider=self.name, transition=f"{self._state.value}->closed")
            self._state = CBState.CLOSED
            self._consecutive_failures = 0
            self._half_open_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._state == CBState.HALF_OPEN:
                self._state = CBState.OPEN
                self._opened_at = time.time()
                self._half_open_in_flight = False
                log_event("circuit", level="WARNING", provider=self.name, transition="half_open->open")
                return
            if self._consecutive_failures >= self.failure_threshold and self._state == CBState.CLOSED:
                self._state = CBState.OPEN
                self._opened_at = time.time()
                log_event(
                    "circuit",
                    level="WARNING",
                    provider=self.name,
                    transition="closed->open",
                    failures=self._consecutive_failures,
                )

    def reset_for_tests(self) -> None:
        with self._lock:
            self._state = CBState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = 0.0
            self._half_open_in_flight = False


_breakers = {
    "gemini": CircuitBreaker("gemini", settings.CIRCUIT_FAILURE_THRESHOLD, settings.CIRCUIT_OPEN_SECONDS),
    "groq":   CircuitBreaker("groq",   settings.CIRCUIT_FAILURE_THRESHOLD, settings.CIRCUIT_OPEN_SECONDS),
    "ollama": CircuitBreaker("ollama", settings.CIRCUIT_FAILURE_THRESHOLD, settings.CIRCUIT_OPEN_SECONDS),
}


def circuit_state(provider: str) -> str:
    cb = _breakers.get(provider)
    return cb.state.value if cb else "unknown"


def reset_circuits_for_tests() -> None:
    for cb in _breakers.values():
        cb.reset_for_tests()


def _force_circuit_for_tests(provider: str, state: CBState) -> None:
    cb = _breakers[provider]
    with cb._lock:  # noqa: SLF001
        cb._state = state                                          # noqa: SLF001
        cb._opened_at = time.time() if state == CBState.OPEN else 0  # noqa: SLF001


# ─── Provider adapters (with timeouts) ───────────────────────────
async def _call_gemini(messages: List[Message], temperature: float, model: str) -> str:
    if not settings.gemini_enabled:
        raise RuntimeError("Gemini disabled (no key)")
    used = gemini_used_today()
    if used >= settings.GEMINI_DAILY_HARD_STOP:
        raise RuntimeError(f"Gemini daily hard-stop reached ({used}/{settings.GEMINI_DAILY_LIMIT})")
    if used >= settings.GEMINI_DAILY_WARN:
        log_event("llm_warn", level="WARNING", provider="gemini", used=used)

    import google.generativeai as genai  # type: ignore

    genai.configure(api_key=settings.GEMINI_API_KEY)
    sys_text = "\n\n".join(m.content for m in messages if m.role == "system") or None
    history = [m for m in messages if m.role != "system"]
    g = genai.GenerativeModel(model_name=model, system_instruction=sys_text)
    chat_input = [
        {"role": "user" if m.role == "user" else "model", "parts": [m.content]} for m in history
    ]

    async def _do() -> str:
        resp = await g.generate_content_async(
            chat_input,
            generation_config={
                "temperature": temperature,
                "max_output_tokens": settings.MAX_REPLY_TOKENS,
            },
        )
        return (resp.text or "").strip()

    text = await asyncio.wait_for(_do(), timeout=settings.GEMINI_TIMEOUT_S)
    _increment_today_counter("gemini")
    return text


async def _call_groq(messages: List[Message], temperature: float, model: str) -> str:
    if not settings.groq_enabled:
        raise RuntimeError("Groq disabled (no key)")
    ok, count = _groq_check_and_increment()
    if not ok:
        raise RuntimeError(f"Groq RPM hard-stop reached ({count}/{settings.GROQ_RPM_LIMIT})")
    if count >= settings.GROQ_RPM_WARN:
        log_event("llm_warn", level="WARNING", provider="groq", rpm=count)

    from groq import AsyncGroq  # type: ignore

    client = AsyncGroq(api_key=settings.GROQ_API_KEY)

    async def _do() -> str:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature,
            max_tokens=settings.MAX_REPLY_TOKENS,
        )
        return ((resp.choices[0].message.content if resp.choices else "") or "").strip()

    return await asyncio.wait_for(_do(), timeout=settings.GROQ_TIMEOUT_S)


async def _call_ollama(messages: List[Message], temperature: float, model: str) -> str:
    if not settings.ollama_enabled:
        raise RuntimeError("Ollama disabled (no base URL)")
    payload = {
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": settings.MAX_REPLY_TOKENS},
    }

    async def _do() -> str:
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.OLLAMA_TIMEOUT_S, connect=3.0)) as c:
            r = await c.post(f"{settings.OLLAMA_BASE_URL}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
        return ((data.get("message") or {}).get("content", "") or "").strip()

    return await asyncio.wait_for(_do(), timeout=settings.OLLAMA_TIMEOUT_S + 2.0)


# ─── Response validation ────────────────────────────────────────
_GENERIC_REFUSAL_RE = re.compile(
    r"^\s*(I'?m sorry,? (?:but )?I (?:can'?t|cannot)|As an AI|I'?m (?:just )?an AI)",
    re.I,
)


def _is_valid_utf8(s: str) -> bool:
    try:
        s.encode("utf-8").decode("utf-8")
        return True
    except UnicodeError:
        return False


def _has_words(s: str) -> bool:
    """At least 2 alphabetic / Devanagari word-like tokens."""
    tokens = re.findall(r"[A-Za-zऀ-ॿ]{2,}", s)
    return len(tokens) >= 2


def _contains_system_fragment(text: str) -> bool:
    from prompts.system_prompt import SYSTEM_PROMPT  # local import to avoid cycle
    fragment = SYSTEM_PROMPT[:80].strip()
    return bool(fragment) and fragment in text


def validate_llm_response(text: str) -> tuple[bool, str]:
    if not text or not text.strip():
        return False, "empty"
    if len(text) < 10:
        return False, "too_short"
    if len(text) > 3000:
        return False, "too_long"
    if not _is_valid_utf8(text):
        return False, "encoding_corruption"
    if _GENERIC_REFUSAL_RE.match(text):
        return False, "generic_refusal"
    if _contains_system_fragment(text):
        return False, "prompt_leak"
    if not _has_words(text):
        return False, "gibberish"
    return True, ""


# ─── Cascade ─────────────────────────────────────────────────────
def _chain(task: TaskType) -> List[tuple[str, str]]:
    if task == "classification":
        return [
            ("groq", settings.GROQ_MODEL_FAST),
            ("gemini", settings.GEMINI_MODEL),
            ("ollama", settings.OLLAMA_MODEL),
        ]
    if task == "reasoning":
        return [
            ("groq", settings.GROQ_MODEL_SMART),
            ("gemini", settings.GEMINI_MODEL),
            ("ollama", settings.OLLAMA_MODEL),
        ]
    if task == "batch":
        return [
            ("ollama", settings.OLLAMA_MODEL),
            ("groq", settings.GROQ_MODEL_FAST),
        ]
    return [
        ("gemini", settings.GEMINI_MODEL),
        ("groq", settings.GROQ_MODEL_SMART),
        ("ollama", settings.OLLAMA_MODEL),
    ]


async def _try_provider(provider: str, model: str, messages: List[Message], temperature: float) -> Tuple[str, int]:
    started = time.perf_counter()
    if provider == "gemini":
        text = await _call_gemini(messages, temperature, model)
    elif provider == "groq":
        text = await _call_groq(messages, temperature, model)
    else:
        text = await _call_ollama(messages, temperature, model)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return text, elapsed_ms


async def generate(
    messages: List[Message],
    task_type: TaskType = "customer_facing",
    temperature: float = 0.6,
) -> LLMResult:
    """Generate a response — cascade providers, validate output, never raise."""
    chain = _chain(task_type)
    last_error: Optional[str] = None
    any_attempted = False

    for provider, model in chain:
        cb = _breakers[provider]
        if not cb.allow():
            log_event("llm_skip", provider=provider, reason="circuit_open")
            continue
        any_attempted = True

        for attempt in (1, 2):
            try:
                text, elapsed = await _try_provider(provider, model, messages, temperature)
            except asyncio.TimeoutError:
                cb.record_failure()
                last_error = f"{provider}:timeout"
                log_event("llm_timeout", level="WARNING", provider=provider, model=model)
                break
            except Exception as e:  # noqa: BLE001
                cb.record_failure()
                last_error = f"{provider}:{e}"
                log_event("llm_fail", level="WARNING", provider=provider, model=model, error=str(e))
                break

            ok, reason = validate_llm_response(text)
            if ok:
                cb.record_success()
                log_event("llm_ok", provider=provider, model=model, latency_ms=elapsed, task=task_type)
                return LLMResult(text=text, model=f"{provider}:{model}", latency_ms=elapsed)

            log_event("llm_invalid", level="WARNING", provider=provider, model=model, reason=reason, attempt=attempt)
            if attempt == 2:
                cb.record_failure()
                last_error = f"{provider}:invalid_response_{reason}"

    if not any_attempted and chain:
        alert_on_error("all_llms_unavailable", {"providers": [p for p, _ in chain]})

    return LLMResult(text="", model="none", latency_ms=0, error=last_error or "no providers configured")


# ─── Snapshot for /api/health ────────────────────────────────────
def usage_snapshot() -> dict:
    return {
        "gemini": {
            "used_today": gemini_used_today(),
            "daily_limit": settings.GEMINI_DAILY_LIMIT,
            "remaining": gemini_remaining_today(),
            "circuit": circuit_state("gemini"),
            "enabled": settings.gemini_enabled,
        },
        "groq": {
            "rpm_current": groq_rpm_current(),
            "rpm_limit": settings.GROQ_RPM_LIMIT,
            "circuit": circuit_state("groq"),
            "enabled": settings.groq_enabled,
        },
        "ollama": {
            "circuit": circuit_state("ollama"),
            "enabled": settings.ollama_enabled,
        },
    }
