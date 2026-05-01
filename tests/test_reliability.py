"""Reliability tests — circuit breaker, response validation, persistence, hot reload."""

import json
import time
import pytest

from core import llm_router
from core.llm_router import CBState, CircuitBreaker, validate_llm_response
from core.conversation import ConversationStore, store as live_store
from services import product_catalog


# ─── Circuit breaker ──────────────────────────────────────────
def test_circuit_opens_after_threshold():
    cb = CircuitBreaker("test", failure_threshold=3, open_seconds=60)
    assert cb.state == CBState.CLOSED
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CBState.CLOSED
    cb.record_failure()
    assert cb.state == CBState.OPEN
    assert cb.allow() is False


def test_circuit_recovers_via_half_open():
    cb = CircuitBreaker("test", failure_threshold=2, open_seconds=60)
    cb.record_failure()
    cb.record_failure()
    # Force the open timer back so the breaker considers the open window expired
    cb._opened_at = time.time() - 100
    assert cb.allow() is True       # transitions to HALF_OPEN
    cb.record_success()
    assert cb._state == CBState.CLOSED


def test_circuit_reopens_if_test_fails():
    cb = CircuitBreaker("test", failure_threshold=1, open_seconds=60)
    cb.record_failure()
    cb._opened_at = time.time() - 100   # simulate window expiry
    assert cb.allow() is True            # HALF_OPEN test request allowed
    cb.record_failure()                  # test request failed
    assert cb._state == CBState.OPEN


# ─── Response validation ──────────────────────────────────────
def test_response_validation_empty():
    ok, reason = validate_llm_response("")
    assert not ok and reason == "empty"


def test_response_validation_too_short():
    ok, reason = validate_llm_response("OK.")
    assert not ok and reason == "too_short"


def test_response_validation_too_long():
    ok, reason = validate_llm_response("a" * 5000)
    assert not ok and reason == "too_long"


def test_response_validation_generic_refusal():
    ok, reason = validate_llm_response("I'm sorry, I cannot help with that request right now.")
    assert not ok and reason == "generic_refusal"


def test_response_validation_passes_real():
    ok, reason = validate_llm_response("Welcome to Kingdom Foods! What kitchen do you run?")
    assert ok


def test_response_validation_gibberish():
    ok, reason = validate_llm_response("$$ ## @@ ££ ¥¥ ££ $$ ## @@")
    assert not ok and reason == "gibberish"


# ─── Persistent rate counter ──────────────────────────────────
def test_persistent_rate_counter_tracks_increments(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_router, "_USAGE_FILE", tmp_path / "u.json")
    n0 = llm_router._read_today_counter("gemini")
    llm_router._increment_today_counter("gemini")
    llm_router._increment_today_counter("gemini")
    n1 = llm_router._read_today_counter("gemini")
    assert n1 == n0 + 2


# ─── Conversation persistence on restart ──────────────────────
def test_conversation_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("core.conversation.CONV_LOG", tmp_path / "conv.jsonl")
    s1 = ConversationStore()
    c = s1.get_or_create(conversation_id="cv_persist1", channel="website")
    c.add("user", "hi")
    c.add("assistant", "hello — what do you need?")
    c.lead_data["business_type"] = "restaurant"
    s1.save(c)

    # Simulate restart
    s2 = ConversationStore()
    s2._loaded = False
    monkeypatch.setattr("core.conversation.CONV_LOG", tmp_path / "conv.jsonl")
    revived = s2.get_or_create(conversation_id="cv_persist1", channel="website")
    assert revived.lead_data.get("business_type") == "restaurant"
    assert any(m.role == "assistant" for m in revived.messages)


def test_conversation_lru_eviction(monkeypatch):
    monkeypatch.setattr("config.settings.MAX_ACTIVE_CONVERSATIONS", 5)
    s = ConversationStore()
    s._loaded = True   # bypass file load
    for i in range(10):
        s.get_or_create(conversation_id=f"cv_lru_{i}", channel="website")
    # Only the 5 most recent remain
    assert len(s._mem) == 5
    assert "cv_lru_0" not in s._mem
    assert "cv_lru_9" in s._mem


def test_stage_no_regression():
    s = ConversationStore()
    s._loaded = True
    c = s.get_or_create(conversation_id="cv_stage", channel="website")
    c.sales_stage = 4
    s.maybe_advance_stage(c, intent="GREETING")    # would normally not advance
    assert c.sales_stage == 4                       # never goes back


def test_stage_resets_on_explicit_request():
    s = ConversationStore()
    s._loaded = True
    c = s.get_or_create(conversation_id="cv_reset", channel="website")
    c.sales_stage = 4
    assert s.maybe_reset(c, "let's start over please")
    assert c.sales_stage == 1


# ─── Catalog hot reload ───────────────────────────────────────
def test_catalog_hot_reload(tmp_path, monkeypatch):
    # Build a tiny custom catalog
    cat_path = tmp_path / "tinycat.json"
    cat_path.write_text(json.dumps([{
        "id": "test-x", "name": "Test Product X", "name_hi": "",
        "category": "test-cat", "type": "RTE",
        "price_per_kg": 100, "price_per_piece": None,
        "moq": 30, "moq_unit": "kg", "pack_sizes": ["1kg"],
        "shelf_life_months": 12, "storage": "frozen",
        "description": "Test only.", "use_cases": [], "store_link": "",
        "image_url": "", "keywords": ["testx"],
    }]), encoding="utf-8")
    monkeypatch.setattr("config.settings.PRODUCTS_JSON_PATH", str(cat_path))

    product_catalog.reload_now()
    assert any(p.id == "test-x" for p in product_catalog.all_products())

    # Mutate the file with different content
    cat_path.write_text(json.dumps([{
        "id": "test-x", "name": "Test Product X", "name_hi": "",
        "category": "test-cat", "type": "RTE",
        "price_per_kg": 200, "price_per_piece": None,    # PRICE CHANGE
        "moq": 30, "moq_unit": "kg", "pack_sizes": ["1kg"],
        "shelf_life_months": 12, "storage": "frozen",
        "description": "Test only.", "use_cases": [], "store_link": "",
        "image_url": "", "keywords": ["testx"],
    }]), encoding="utf-8")
    product_catalog.reload_now()
    p = product_catalog.by_id("test-x")
    assert p and p.price_per_kg == 200


def test_levenshtein_typo_tolerance():
    assert product_catalog.levenshtein("momos", "momos") == 0
    assert product_catalog.levenshtein("mmos", "momos") == 1
    assert product_catalog.levenshtein("samsa", "samosa") == 1
    # panir → paneer: substitute i→e + insert e = 2 ops
    assert product_catalog.levenshtein("panir", "paneer") == 2
    assert product_catalog.levenshtein("paner", "paneer") == 1


def test_fuzzy_search_finds_with_typo():
    # Force seed catalog (pinned in conftest)
    product_catalog.reload_now()
    res = product_catalog.search("paner momos")     # typo in 'paneer'
    assert res
    assert any("momo" in p.id.lower() or "paneer" in p.name.lower() for p in res)
