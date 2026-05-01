import asyncio

import pytest

from core.conversation import store
from core.lead_tracker import extract_and_update, missing_fields, score


def test_score_weights_complete():
    full = {
        "business_type": "hotel",
        "outlet_name": "Sharma Kitchen",
        "city": "Noida",
        "phone": "9876543210",
        "volume": "200 kg",
        "storage": "frozen",
        "decision_maker": "true",
    }
    assert score(full) == 100


def test_missing_fields():
    assert "phone" in missing_fields({"city": "Delhi"})


@pytest.mark.asyncio
async def test_extract_phone_and_city():
    conv = store.get_or_create(channel="website")
    diff = await extract_and_update(conv, "Hi I'm calling from Mumbai, my number is 9876543210, we run a cloud kitchen")
    assert "phone" in diff
    assert "city" in diff
    assert conv.lead_data.get("business_type") == "cloud_kitchen"
    assert conv.lead_data.get("phone") == "9876543210"


@pytest.mark.asyncio
async def test_extract_volume_in_kg_and_tons():
    conv = store.get_or_create(channel="website")
    await extract_and_update(conv, "We need around 1.5 tons monthly")
    assert conv.lead_data["volume"].endswith("kg")
    n = int(conv.lead_data["volume"].split()[0])
    assert 1400 <= n <= 1600
