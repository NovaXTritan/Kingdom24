"""Quick sanity checks for Hindi/Hinglish handling."""

import pytest

from channels.website import ChatRequest, Metadata, handle_chat_request
from core.intent_classifier import _keyword_intent
from services import product_catalog


def test_hindi_keyword_classification():
    assert _keyword_intent("price kya hai bhai?") == "INQUIRY"
    assert _keyword_intent("ghar ke liye 5kg chahiye") == "NON_B2B"
    assert _keyword_intent("samosa ka order karna hai") == "ORDER"


def test_hinglish_search_finds_products():
    # User typed Hinglish — should still find matches
    res = product_catalog.search("paneer momos chahiye delhi mein")
    assert res
    assert res[0].category == "frozen-momos"


@pytest.mark.asyncio
async def test_hinglish_full_flow():
    req = ChatRequest(
        conversation_id="t-hi-1",
        message="Bhai 200 kilo veg momos chahiye, monthly. Cloud kitchen Delhi mein chalata hoon",
        metadata=Metadata(page_url="/"),
    )
    out = await handle_chat_request(req)
    # Lead should have business_type, city, volume
    assert out["lead_score"] >= 40
