"""Tests for chatbot intelligence — language, off-topic, food relevance,
pricing gate, name extraction.

These cover the "Tier 1 intelligence rebuild" — the five behaviours that make
the chatbot stop hallucinating.
"""

import pytest

from core.intent_classifier import (
    classify,
    detect_language,
    is_food_or_business_message,
)
from core.lead_tracker import extract_name


# ─── Language detection ──────────────────────────────────────────
def test_english_detected():
    assert detect_language("I would like to explore your product range") == "ENGLISH"


def test_devanagari_detected_as_hindi():
    assert detect_language("मुझे 200 किलो पनीर चाहिए") == "HINDI"


def test_pure_hindi_romanized_detected_as_hinglish():
    """Romanized Hindi without English content lands in HINGLISH bucket
    (we treat romanized Hindi + sparse English as the same response style)."""
    assert detect_language("bhai mujhe 200 kilo momos chahiye") == "HINGLISH"


def test_pure_english_with_indian_city_stays_english():
    """Mentioning Mumbai/Delhi must NOT trip Hindi detection."""
    assert detect_language("What frozen products do you offer for hotels in Mumbai?") == "ENGLISH"


def test_empty_message_default_english():
    assert detect_language("") == "ENGLISH"
    assert detect_language("   ") == "ENGLISH"


# ─── Food/business relevance ─────────────────────────────────────
def test_time_management_not_food():
    assert is_food_or_business_message("I need help with time management") is False


def test_momos_is_food():
    assert is_food_or_business_message("I need momos for my restaurant") is True


def test_hotel_is_business():
    assert is_food_or_business_message("I run a hotel in Mumbai") is True


def test_qty_pattern_counts_as_food():
    """A bare quantity like '50 kg' indicates a food order even without
    product name keywords."""
    assert is_food_or_business_message("Quote me 50 kg please") is True


def test_price_mention_counts_as_food():
    assert is_food_or_business_message("Whats the price ₹ for bulk") is True


# ─── Off-topic detection ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_time_management_is_off_topic():
    assert await classify("I need help with time management") == "OFF_TOPIC"


@pytest.mark.asyncio
async def test_math_homework_is_off_topic():
    assert await classify("Can you solve this quadratic equation for me please") == "OFF_TOPIC"


@pytest.mark.asyncio
async def test_weather_is_off_topic():
    assert await classify("What is the weather going to be in Delhi today") == "OFF_TOPIC"


@pytest.mark.asyncio
async def test_momos_is_not_off_topic():
    assert await classify("I need 200 kg momos for my restaurant") != "OFF_TOPIC"


@pytest.mark.asyncio
async def test_hotel_inquiry_is_not_off_topic():
    assert await classify("What products do you have for hotels?") != "OFF_TOPIC"


@pytest.mark.asyncio
async def test_short_greeting_is_not_off_topic():
    """Short follow-ups like 'hi' or 'yes' must bypass the relevance check."""
    assert await classify("Hello") != "OFF_TOPIC"
    assert await classify("yes") != "OFF_TOPIC"


# ─── Name extraction ─────────────────────────────────────────────
def test_extract_name_im_pattern():
    assert extract_name("Hi, I'm Raj from Sharma Foods") == "Raj"


def test_extract_name_my_name_is_pattern():
    assert extract_name("My name is Priya, I run a cloud kitchen") == "Priya"


def test_extract_name_mera_naam_pattern():
    assert extract_name("mera naam Karan hai, hotel chalata hoon") == "Karan"


def test_extract_name_returns_none_when_absent():
    assert extract_name("I want a quote for momos") is None


def test_extract_name_skips_common_starter_words():
    """'Looking' starts with capital but is not a name."""
    assert extract_name("Looking for frozen gravies") is None


# ─── End-to-end: language preserved through templates ───────────
def test_off_topic_template_in_english():
    from prompts.templates import get_template
    eng = get_template("OFF_TOPIC", "ENGLISH")
    assert "kingdom" in eng.lower() or "restaurant" in eng.lower()
    assert "aap" not in eng.lower()  # no Hindi leak


def test_off_topic_template_in_hindi_has_hindi_words():
    from prompts.templates import get_template
    hi = get_template("OFF_TOPIC", "HINDI")
    # At least one Hindi marker word present
    assert any(w in hi.lower() for w in ("aap", "hain", "hoon", "kya"))


def test_pricing_gate_template_exists_in_all_languages():
    from prompts.templates import get_template
    for lang in ("ENGLISH", "HINDI", "HINGLISH"):
        t = get_template("PRICING_GATE", lang)
        assert t and len(t) > 30
