"""Delivery + packaging cost calculator.

Encodes the rules from the K24 shipping policy. All amounts in INR (rupees).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from config import DATA_DIR


@dataclass
class DeliveryCost:
    packaging: int
    shipping: int
    total: int
    notes: str


@lru_cache
def _zones() -> dict:
    with (DATA_DIR / "delivery_zones.json").open("r", encoding="utf-8") as f:
        return json.load(f)


def _classify_city(city: str) -> str:
    if not city:
        return "other"
    c = city.strip().lower()
    z = _zones()
    if c in (x.lower() for x in z["noida"]):
        return "noida"
    if c in (x.lower() for x in z["ncr"]):
        return "ncr"
    if c in (x.lower() for x in z["metros"]):
        return "metro"
    return "other"


def calculate(
    city: str,
    weight_kg: float,
    is_frozen: bool,
    order_value: Optional[int] = None,
) -> DeliveryCost:
    zone = _classify_city(city)

    # Packaging: ₹800 per 30kg batch of frozen, ₹0 ambient
    packaging = 0
    if is_frozen and weight_kg > 0:
        batches = max(1, -(-int(weight_kg) // 30))  # ceil division
        packaging = 800 * batches

    # Shipping
    if zone == "noida":
        shipping = 0 if (order_value or 0) >= 2000 else 150
        notes = "Free delivery in Noida above ₹2,000."
    elif zone == "ncr":
        # ₹15/kg for first 30kg, ₹12/kg above
        first = min(30, weight_kg)
        rest = max(0, weight_kg - 30)
        shipping = round(first * 15 + rest * 12)
        notes = "Actual courier (Porter / Shadowfax) — typical NCR consignment."
    elif zone == "metro":
        # ₹35/kg, minimum 30kg, batched in 30kg packs
        billable_kg = max(30, -(-int(weight_kg) // 30) * 30)
        shipping = billable_kg * 35
        notes = f"Reefer (Snowman / Gati Kausar): ₹35/kg, min 30 kg per batch ({billable_kg} kg billable)."
    else:
        shipping = 0
        notes = (
            "Outside Tier-1 metro — shipping is quoted on confirmation. "
            "Indicative range ₹40–60/kg via reefer aggregator."
        )

    return DeliveryCost(
        packaging=packaging,
        shipping=shipping,
        total=packaging + shipping,
        notes=notes,
    )
