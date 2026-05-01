"""End-to-end: invoke the chat handler with no LLM keys configured.

The deterministic responder should kick in and return a useful payload —
that's what proves the service runs without API keys.
"""

import pytest

from channels.website import ChatRequest, Metadata, handle_chat_request


@pytest.mark.asyncio
async def test_greeting_returns_actions():
    req = ChatRequest(conversation_id="t-greet", message="__greeting__", metadata=Metadata(page_url="/"))
    out = await handle_chat_request(req)
    assert out["reply"]
    assert isinstance(out["actions"], list) and out["actions"]


@pytest.mark.asyncio
async def test_non_b2b_redirect():
    req = ChatRequest(conversation_id="t-nonb2b", message="I want momos for a small house party", metadata=Metadata())
    out = await handle_chat_request(req)
    assert "B2B" in out["reply"] or "kingdom24.in" in out["reply"]


@pytest.mark.asyncio
async def test_contact_template():
    req = ChatRequest(conversation_id="t-contact", message="Aapka phone number kya hai?", metadata=Metadata())
    out = await handle_chat_request(req)
    assert "8800804580" in out["reply"]


@pytest.mark.asyncio
async def test_quote_returns_products():
    # No keys configured → falls through to deterministic responder, which
    # should still return product cards + tier-aware pricing
    req = ChatRequest(conversation_id="t-quote", message="Quote dal makhani for 100 kg per month", metadata=Metadata())
    out = await handle_chat_request(req)
    assert out["reply"]
    assert isinstance(out["products"], list)
