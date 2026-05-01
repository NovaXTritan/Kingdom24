import pytest

from core.intent_classifier import _keyword_intent, classify


def test_keyword_greeting():
    assert _keyword_intent("Hi there") == "GREETING"
    assert _keyword_intent("Namaste") == "GREETING"


def test_keyword_contact():
    assert _keyword_intent("Whatsapp number please") == "CONTACT"
    assert _keyword_intent("Aapka pata kya hai?") == "CONTACT"


def test_keyword_non_b2b():
    assert _keyword_intent("Can I order momos for a house party?") == "NON_B2B"
    assert _keyword_intent("Ghar ke liye chahiye") == "NON_B2B"


def test_keyword_payment():
    assert _keyword_intent("Razorpay link bhejo") == "PAYMENT"


def test_keyword_inquiry():
    assert _keyword_intent("What are your prices?") == "INQUIRY"


@pytest.mark.asyncio
async def test_classify_via_keyword_path():
    # No LLM needed — keyword should resolve
    assert await classify("Namaste, prices kya hain?") in {"GREETING", "INQUIRY"}
