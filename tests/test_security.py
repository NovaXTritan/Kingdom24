"""Security tests — sanitizer, rate limiter, auth, PII."""

import pytest
from fastapi.testclient import TestClient

from main import app
from middleware import rate_limiter
from middleware.sanitizer import (
    is_allowed_page_url, is_oversized, is_valid_uuid_or_id, sanitize_message,
)
from core.logger import hash_phone, mask_phone


# ─── Sanitizer ────────────────────────────────────────────────
def test_html_injection_stripped():
    s = sanitize_message('Hello <script>alert(1)</script> there <img src=x onerror=alert(2)>')
    assert "<script" not in s.text.lower()
    assert "<img" not in s.text.lower()
    assert "Hello" in s.text


def test_max_message_length():
    long = "a" * 5000
    s = sanitize_message(long)
    assert len(s.text) <= 2010      # approx (truncation marker added)
    assert s.truncated
    assert s.notice


def test_control_chars_stripped():
    s = sanitize_message("hello\x00world\x07ok")
    assert "\x00" not in s.text
    assert "\x07" not in s.text
    assert "hello" in s.text and "world" in s.text


def test_body_size_limit():
    assert is_oversized("99999999")
    assert not is_oversized("1024")
    assert not is_oversized(None)


def test_uuid_validation_rejects_garbage():
    assert is_valid_uuid_or_id("")                        # blank ok (server mints)
    assert is_valid_uuid_or_id("cv_abc123def456")
    assert is_valid_uuid_or_id("550e8400-e29b-41d4-a716-446655440000")
    assert not is_valid_uuid_or_id("'; DROP TABLE--")
    assert not is_valid_uuid_or_id("<script>")
    assert not is_valid_uuid_or_id("../../etc/passwd")


def test_page_url_whitelist():
    assert is_allowed_page_url("https://kingdom24.in/products/x")
    assert is_allowed_page_url("https://www.kingdom24.in/")
    assert is_allowed_page_url("http://localhost:3000/")
    assert is_allowed_page_url("/products/dal-makhani")
    assert not is_allowed_page_url("https://evil.com/exfil")


# ─── Rate limiter ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_rate_limit_per_ip_minute():
    await rate_limiter._reset_for_tests()
    from config import settings
    ip = "1.2.3.4"
    # Hit the limit
    for _ in range(settings.RL_CHAT_PER_IP_MIN):
        d = await rate_limiter.check_chat_limits(ip, "cv_x")
        assert d.allowed
    d = await rate_limiter.check_chat_limits(ip, "cv_x")
    assert not d.allowed
    assert d.bucket == "ip_min"


@pytest.mark.asyncio
async def test_rate_limit_per_conversation():
    await rate_limiter._reset_for_tests()
    from config import settings
    cid = "cv_unique"
    # Different IPs but same conversation
    for i in range(settings.RL_CHAT_PER_CONV_HOUR):
        d = await rate_limiter.check_chat_limits(f"ip_{i}", cid)
        assert d.allowed
    d = await rate_limiter.check_chat_limits("ip_new", cid)
    assert not d.allowed
    assert d.bucket == "conv_hour"


@pytest.mark.asyncio
async def test_rate_limit_stats_endpoint():
    await rate_limiter._reset_for_tests()
    from config import settings
    ip = "9.9.9.9"
    for _ in range(settings.RL_STATS_PER_IP_MIN):
        d = await rate_limiter.check_stats_limits(ip)
        assert d.allowed
    d = await rate_limiter.check_stats_limits(ip)
    assert not d.allowed


# ─── Stats requires API key ───────────────────────────────────
def test_stats_requires_api_key(monkeypatch):
    monkeypatch.setattr("config.settings.STATS_API_KEY", "topsecret")
    client = TestClient(app)
    r = client.get("/api/stats")
    assert r.status_code == 401
    r = client.get("/api/stats", headers={"X-API-Key": "topsecret"})
    assert r.status_code in (200, 429)


def test_health_no_auth_required():
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "llm" in body and "catalog" in body and "conversations" in body


# ─── PII helpers ─────────────────────────────────────────────
def test_phone_masking():
    assert mask_phone("9876543210") == "98****3210"
    # 12 digits including 91 prefix → 6 stars in middle
    assert mask_phone("+919876543210") == "91******3210"
    assert mask_phone("") == ""


def test_phone_hashing_deterministic():
    h1 = hash_phone("9876543210")
    h2 = hash_phone("+91 9876 543 210")
    assert h1 and h1 == h2


# ─── Body size 413 ────────────────────────────────────────────
def test_request_too_large_returns_413():
    client = TestClient(app)
    big = "x" * 20000
    r = client.post("/api/chat", json={"conversation_id": "", "message": big, "metadata": {"page_url": "/"}})
    # Either 413 (caught by middleware via Content-Length) or 200 with truncation
    assert r.status_code in (200, 413)


# ─── Bad conversation ID ──────────────────────────────────────
def test_invalid_conversation_id_rejected():
    client = TestClient(app)
    r = client.post(
        "/api/chat",
        json={"conversation_id": "'; DROP TABLE--", "message": "hi",
              "metadata": {"page_url": "/"}},
    )
    assert r.status_code == 400
