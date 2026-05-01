"""In-memory sliding-window rate limiter.

Single-process, no Redis. Three independent buckets per request:
   1. per IP per minute
   2. per IP per hour
   3. per conversation per hour
   4. global per day (covers Gemini quota guard)

Decision: returned by ``check_chat_limits(ip, conv_id)`` as a `RateDecision`
that bundles `allowed`, `reason`, `retry_after`. The middleware converts that
into HTTP 429 with the friendly Hindi/English error.

Cleanup: amortised — every Nth check sweeps expired entries.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Tuple

from config import settings
from core.logger import log_event

_lock = asyncio.Lock()

# Each bucket is a deque of timestamps. We trim entries older than the window.
_bucket_chat_ip_min: Dict[str, Deque[float]] = defaultdict(deque)
_bucket_chat_ip_hour: Dict[str, Deque[float]] = defaultdict(deque)
_bucket_chat_conv_hour: Dict[str, Deque[float]] = defaultdict(deque)
_bucket_global_day: Deque[float] = deque()
_bucket_wa_phone_min: Dict[str, Deque[float]] = defaultdict(deque)
_bucket_stats_ip_min: Dict[str, Deque[float]] = defaultdict(deque)

_check_count = 0


@dataclass
class RateDecision:
    allowed: bool
    reason: str = ""
    retry_after: int = 0
    bucket: str = ""


def _trim(window: Deque[float], horizon_seconds: int, now: float) -> None:
    cutoff = now - horizon_seconds
    while window and window[0] < cutoff:
        window.popleft()


def _trim_all_dict(d: Dict[str, Deque[float]], horizon: int, now: float) -> None:
    drop_keys = []
    for k, dq in d.items():
        _trim(dq, horizon, now)
        if not dq:
            drop_keys.append(k)
    for k in drop_keys:
        del d[k]


def _amortised_sweep(now: float) -> None:
    global _check_count
    _check_count += 1
    if _check_count % 100:
        return
    _trim_all_dict(_bucket_chat_ip_min, 60, now)
    _trim_all_dict(_bucket_chat_ip_hour, 3600, now)
    _trim_all_dict(_bucket_chat_conv_hour, 3600, now)
    _trim_all_dict(_bucket_wa_phone_min, 60, now)
    _trim_all_dict(_bucket_stats_ip_min, 60, now)
    _trim(_bucket_global_day, 86400, now)


# ─── Public API ──────────────────────────────────────────────────
async def check_chat_limits(ip: str, conv_id: str | None = None) -> RateDecision:
    now = time.time()
    async with _lock:
        _amortised_sweep(now)

        # Global daily — checked first (cheap, fail-fast)
        _trim(_bucket_global_day, 86400, now)
        if len(_bucket_global_day) >= settings.RL_CHAT_GLOBAL_DAILY:
            log_event("rate_limit", level="WARNING", bucket="global_day", count=len(_bucket_global_day))
            return RateDecision(False, "global_daily_quota", retry_after=3600, bucket="global_day")

        # Per-IP / minute
        b1 = _bucket_chat_ip_min[ip]
        _trim(b1, 60, now)
        if len(b1) >= settings.RL_CHAT_PER_IP_MIN:
            log_event("rate_limit", level="INFO", bucket="ip_min", ip=ip, count=len(b1))
            return RateDecision(False, "too_many_per_minute", retry_after=60, bucket="ip_min")

        # Per-IP / hour
        b2 = _bucket_chat_ip_hour[ip]
        _trim(b2, 3600, now)
        if len(b2) >= settings.RL_CHAT_PER_IP_HOUR:
            log_event("rate_limit", level="INFO", bucket="ip_hour", ip=ip, count=len(b2))
            return RateDecision(False, "too_many_per_hour", retry_after=600, bucket="ip_hour")

        # Per-conv / hour
        if conv_id:
            b3 = _bucket_chat_conv_hour[conv_id]
            _trim(b3, 3600, now)
            if len(b3) >= settings.RL_CHAT_PER_CONV_HOUR:
                log_event("rate_limit", level="INFO", bucket="conv_hour", conv_id=conv_id, count=len(b3))
                return RateDecision(False, "too_many_per_conversation", retry_after=600, bucket="conv_hour")
            b3.append(now)

        b1.append(now)
        b2.append(now)
        _bucket_global_day.append(now)
    return RateDecision(True)


async def check_whatsapp_limits(phone: str) -> RateDecision:
    now = time.time()
    async with _lock:
        _amortised_sweep(now)
        b = _bucket_wa_phone_min[phone]
        _trim(b, 60, now)
        if len(b) >= settings.RL_WHATSAPP_PER_PHONE_MIN:
            return RateDecision(False, "too_many_per_minute", retry_after=60, bucket="wa_phone_min")
        b.append(now)
    return RateDecision(True)


async def check_stats_limits(ip: str) -> RateDecision:
    now = time.time()
    async with _lock:
        _amortised_sweep(now)
        b = _bucket_stats_ip_min[ip]
        _trim(b, 60, now)
        if len(b) >= settings.RL_STATS_PER_IP_MIN:
            return RateDecision(False, "too_many_per_minute", retry_after=60, bucket="stats_ip_min")
        b.append(now)
    return RateDecision(True)


# ─── Test helpers ────────────────────────────────────────────────
async def _reset_for_tests() -> None:
    async with _lock:
        for d in (
            _bucket_chat_ip_min, _bucket_chat_ip_hour, _bucket_chat_conv_hour,
            _bucket_wa_phone_min, _bucket_stats_ip_min,
        ):
            d.clear()
        _bucket_global_day.clear()


def snapshot() -> dict:
    """For health endpoint."""
    return {
        "ip_min_keys": len(_bucket_chat_ip_min),
        "global_today": len(_bucket_global_day),
        "global_daily_limit": settings.RL_CHAT_GLOBAL_DAILY,
    }
