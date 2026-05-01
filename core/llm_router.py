"""Multi-LLM router with cascading failover.

Cascades by task type:
    customer_facing → Gemini Flash → Groq 70B → Ollama → deterministic
    classification  → Groq 8B      → Gemini   → Ollama → keyword fallback
    reasoning       → Groq 70B     → Gemini   → Ollama → deterministic
    batch           → Ollama only

Tracks daily Gemini usage and per-minute Groq usage. When a provider hits its
limit (or fails), the router transparently moves to the next one.

Returns a `LLMResult` with the model name actually used and latency, so
upstream code can attribute responses for analytics.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import List, Literal, Optional

import httpx

from config import settings

log = logging.getLogger("k24.llm")

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


# ─── Rate limiting ────────────────────────────────────────────────
class _Limiter:
    """In-memory rate counters. Loud and simple; perfect for one process."""

    def __init__(self) -> None:
        self.gemini_day: date = date.today()
        self.gemini_count: int = 0
        self.groq_minute: int = int(time.time() // 60)
        self.groq_count: int = 0

    def gemini_allow(self) -> bool:
        today = date.today()
        if today != self.gemini_day:
            self.gemini_day = today
            self.gemini_count = 0
        return self.gemini_count < settings.GEMINI_DAILY_LIMIT

    def gemini_record(self) -> None:
        self.gemini_count += 1

    def groq_allow(self) -> bool:
        m = int(time.time() // 60)
        if m != self.groq_minute:
            self.groq_minute = m
            self.groq_count = 0
        return self.groq_count < settings.GROQ_RPM_LIMIT

    def groq_record(self) -> None:
        self.groq_count += 1


_limiter = _Limiter()


# ─── Provider adapters ────────────────────────────────────────────
async def _call_gemini(messages: List[Message], temperature: float, model: str) -> str:
    if not settings.gemini_enabled:
        raise RuntimeError("Gemini disabled (no key)")
    if not _limiter.gemini_allow():
        raise RuntimeError("Gemini daily limit reached")
    # Lazy import keeps cold-start fast for users without the key.
    import google.generativeai as genai  # type: ignore

    genai.configure(api_key=settings.GEMINI_API_KEY)
    sys_text = "\n\n".join(m.content for m in messages if m.role == "system") or None
    history = [m for m in messages if m.role != "system"]
    g = genai.GenerativeModel(model_name=model, system_instruction=sys_text)
    chat_input = []
    for m in history:
        chat_input.append({"role": "user" if m.role == "user" else "model", "parts": [m.content]})
    resp = await g.generate_content_async(
        chat_input,
        generation_config={
            "temperature": temperature,
            "max_output_tokens": settings.MAX_REPLY_TOKENS,
        },
    )
    _limiter.gemini_record()
    return (resp.text or "").strip()


async def _call_groq(messages: List[Message], temperature: float, model: str) -> str:
    if not settings.groq_enabled:
        raise RuntimeError("Groq disabled (no key)")
    if not _limiter.groq_allow():
        await asyncio.sleep(0.6)  # gentle backoff
        if not _limiter.groq_allow():
            raise RuntimeError("Groq RPM limit reached")
    from groq import AsyncGroq  # type: ignore

    client = AsyncGroq(api_key=settings.GROQ_API_KEY)
    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": m.role, "content": m.content} for m in messages],
        temperature=temperature,
        max_tokens=settings.MAX_REPLY_TOKENS,
    )
    _limiter.groq_record()
    text = resp.choices[0].message.content if resp.choices else ""
    return (text or "").strip()


async def _call_ollama(messages: List[Message], temperature: float, model: str) -> str:
    if not settings.ollama_enabled:
        raise RuntimeError("Ollama disabled (no base URL)")
    payload = {
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": settings.MAX_REPLY_TOKENS},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=3.0)) as c:
        r = await c.post(f"{settings.OLLAMA_BASE_URL}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
    text = (data.get("message") or {}).get("content", "")
    return (text or "").strip()


# ─── Router ───────────────────────────────────────────────────────
def _chain(task: TaskType) -> List[tuple[str, str]]:
    """Return ordered (provider, model) tuples for the cascade."""
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
    # customer_facing — Gemini first for best multilingual quality
    return [
        ("gemini", settings.GEMINI_MODEL),
        ("groq", settings.GROQ_MODEL_SMART),
        ("ollama", settings.OLLAMA_MODEL),
    ]


async def generate(
    messages: List[Message],
    task_type: TaskType = "customer_facing",
    temperature: float = 0.6,
) -> LLMResult:
    """Generate a response, trying each provider in cascade until one succeeds.

    Never raises — always returns a `LLMResult`. If every provider fails, the
    caller can detect this via `result.error` and use a deterministic fallback.
    """
    chain = _chain(task_type)
    last_error: Optional[str] = None
    for provider, model in chain:
        started = time.perf_counter()
        try:
            if provider == "gemini":
                text = await _call_gemini(messages, temperature, model)
            elif provider == "groq":
                text = await _call_groq(messages, temperature, model)
            else:
                text = await _call_ollama(messages, temperature, model)

            elapsed = int((time.perf_counter() - started) * 1000)
            if not text:
                last_error = f"{provider} returned empty"
                log.warning("llm_empty", extra={"provider": provider, "model": model})
                continue
            log.info(
                "llm_ok",
                extra={"provider": provider, "model": model, "latency_ms": elapsed, "task": task_type},
            )
            return LLMResult(text=text, model=f"{provider}:{model}", latency_ms=elapsed)
        except Exception as e:  # noqa: BLE001 — we explicitly want to swallow & cascade
            last_error = f"{provider}: {e}"
            log.warning(
                "llm_fail", extra={"provider": provider, "model": model, "error": str(e)}
            )
            continue

    return LLMResult(text="", model="none", latency_ms=0, error=last_error or "no providers configured")


# ─── Helpers used by tests / health-check ─────────────────────────
def usage_snapshot() -> dict:
    return {
        "gemini_used_today": _limiter.gemini_count,
        "gemini_daily_limit": settings.GEMINI_DAILY_LIMIT,
        "groq_minute_count": _limiter.groq_count,
        "groq_rpm_limit": settings.GROQ_RPM_LIMIT,
        "providers_enabled": {
            "gemini": settings.gemini_enabled,
            "groq": settings.groq_enabled,
            "ollama": settings.ollama_enabled,
        },
    }
