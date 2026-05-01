"""Deterministic responder used when every LLM provider fails.

Mirrors the in-app /api/chat brain on the Next.js side: tier-aware pricing,
product cards, payment-link composition. Keeps the service usable even with
zero API keys configured.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from prompts.templates import NO_PROVIDER_FALLBACK, UNKNOWN_PRODUCT
from services import delivery_calculator, razorpay_links


def _tier(qty_kg: int) -> tuple[str, float]:
    if qty_kg >= 300:
        return ("300 kg+", 0.85)
    if qty_kg >= 100:
        return ("100–299 kg", 0.92)
    if qty_kg >= 25:
        return ("25–99 kg", 0.95)
    return ("Up to 25 kg", 1.0)


def _extract_qty(text: str) -> Optional[int]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|kilo|ton|tonne|tonnes)", text, re.I)
    if not m:
        return None
    n = float(m.group(1))
    if m.group(2).lower().startswith("ton"):
        n *= 1000
    return int(round(n))


def respond(
    intent: str,
    message: str,
    conv_lead: dict,
    products: List[dict],
) -> Tuple[str, list, Optional[dict], list]:
    """Return (reply_text, products_for_widget, payment_card, quick_actions)."""

    # Greeting / unknown providers, still serve a useful answer
    if intent == "GREETING":
        return (
            NO_PROVIDER_FALLBACK,
            [],
            None,
            [
                {"label": "Browse bestsellers", "message": "Show me your bestsellers"},
                {"label": "Get a quote",        "message": "I want a bulk quote"},
                {"label": "Talk to sales",      "message": "OPEN_WHATSAPP"},
            ],
        )

    # Pricing / quote intent
    if intent in {"ORDER", "INQUIRY", "PAYMENT", "GENERAL"} and products:
        qty = _extract_qty(message) or 100
        tier_label, mult = _tier(qty)

        lines = []
        for p in products[:3]:
            per_kg = round((p.get("price_per_kg") or 0) * mult)
            total = per_kg * qty
            lines.append(f"· {p['name']}\n  Tier {tier_label}: ₹{per_kg}/kg → ₹{total:,} for {qty} kg")

        text = (
            f"Tier {tier_label} pricing on the closest matches:\n\n"
            + "\n".join(lines) +
            "\n\nSab indicative B2B + GST. Want to set up a trial order? "
            "Bataaiye outlet name, city aur phone — main quote final kar deta hoon."
        )

        # If user asked for payment AND we have all key fields, add the card
        payment = None
        if intent == "PAYMENT" and conv_lead.get("phone") and conv_lead.get("city") and products:
            p0 = products[0]
            per_kg = round((p0.get("price_per_kg") or 0) * mult)
            order_value = per_kg * qty
            d = delivery_calculator.calculate(
                conv_lead.get("city", ""),
                qty,
                p0.get("storage") == "frozen",
                order_value,
            )
            total = order_value + d.total
            payment = razorpay_links.to_card(
                amount=total,
                description=f"Trial: {p0['name']} × {qty}kg @ ₹{per_kg}/kg",
                name=conv_lead.get("outlet_name") or "Order",
                phone=conv_lead.get("phone"),
                email=conv_lead.get("email"),
            )

        return (
            text,
            _shape(products),
            payment,
            [
                {"label": f"Quote for {qty * 2} kg", "message": f"Quote for {qty * 2} kg per month"},
                {"label": "Set up a 30 kg trial",   "message": "Set up a 30 kg trial order"},
                {"label": "Talk to sales",          "message": "OPEN_WHATSAPP"},
            ],
        )

    if intent == "SAMPLE":
        return (
            "Trial orders are ideal before bulk PO. Most customers start with a 5–10 kg sample bundle.\n\n"
            "Share your business name, delivery city and a phone number — sales team will arrange "
            "samples within 24 hours.",
            _shape(products),
            None,
            [
                {"label": "I run a restaurant", "message": "Restaurant in NCR — please quote a 10 kg sample"},
                {"label": "I run a hotel",      "message": "Hotel — please quote a 10 kg sample bundle"},
                {"label": "Talk to sales",      "message": "OPEN_WHATSAPP"},
            ],
        )

    # No products matched — bounce gently
    return (
        UNKNOWN_PRODUCT,
        [],
        None,
        [
            {"label": "Browse momos",   "message": "Show me momos"},
            {"label": "Browse gravies", "message": "Show me frozen gravies"},
            {"label": "Talk to sales",  "message": "OPEN_WHATSAPP"},
        ],
    )


def _shape(products: List[dict]) -> list[dict]:
    out = []
    for p in products[:4]:
        out.append({
            "slug": p["id"],
            "name": p["name"],
            "category": p["category"],
            "image": p.get("image_url") or "",
            "price": p.get("price_per_kg") or 0,
            "pack": ", ".join(p["pack_sizes"][:2]),
            "moq": f"MOQ {p['moq']}{p['moq_unit']}",
        })
    return out
