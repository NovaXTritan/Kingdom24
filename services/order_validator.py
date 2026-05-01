"""Order validation — runs *before* a Razorpay link is generated.

Pure functions. The chat handler asks `validate_order(...)` and either
proceeds with the calculated total or surfaces the friendly error to the
customer.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import List, Optional

from config import settings
from services import delivery_calculator, product_catalog


@dataclass
class OrderItem:
    product_id: str
    quantity_kg: float


@dataclass
class OrderValidation:
    ok: bool
    reason: str = ""
    message: str = ""
    subtotal: int = 0
    packaging: int = 0
    delivery: int = 0
    total: int = 0
    has_frozen: bool = False
    flagged_high_value: bool = False
    similar_products: List[str] = None  # type: ignore[assignment]


def _zone(city: str) -> str:
    from services.delivery_calculator import _zones, _classify_city  # noqa: WPS437

    return _classify_city(city)


def _city_serviceable(city: str) -> bool:
    if not city:
        return False
    return _zone(city) in {"noida", "ncr", "metro"}


def validate_order(
    items: List[OrderItem],
    city: str,
    *,
    llm_total: Optional[int] = None,
) -> OrderValidation:
    """Run every pre-payment check. Always returns a result — never raises."""
    if not items:
        return OrderValidation(ok=False, reason="no_items", message="Aap kaunsa product order karna chahte hain?")

    # 1. Resolve products
    resolved = []
    missing = []
    for it in items:
        p = product_catalog.by_id(it.product_id)
        if not p:
            missing.append(it.product_id)
        else:
            resolved.append((it, p))

    if missing:
        # Suggest similar
        sims = []
        for q in missing[:1]:
            for p in product_catalog.search(q, limit=3):
                sims.append(p.name)
        return OrderValidation(
            ok=False,
            reason="product_not_found",
            message=(
                f"'{', '.join(missing)}' humari catalog mein nahi mila. "
                + (f"Kya aap ye dekhna chahenge: {', '.join(sims)}?" if sims else "Catalog: https://kingdom24.in")
            ),
            similar_products=sims,
        )

    # 2. Compute totals
    subtotal = 0
    total_kg = 0.0
    has_frozen = False
    moq_violations: List[str] = []
    for it, p in resolved:
        qty = float(it.quantity_kg)
        if p.storage == "frozen":
            has_frozen = True
            moq = max(p.moq, settings.DEFAULT_FROZEN_MOQ_KG)
            if qty < moq:
                moq_violations.append(f"{p.name}: {qty} kg (MOQ {moq} kg)")
        subtotal += int(round((p.price_per_kg or 0) * qty))
        total_kg += qty

    if moq_violations:
        return OrderValidation(
            ok=False,
            reason="below_moq",
            message=(
                "Frozen items ka MOQ 30 kg per SKU per dispatch hai:\n"
                + "\n".join(f"· {v}" for v in moq_violations)
                + "\n\nQuantity badha do, ya ek alag SKU add karo jo 30 kg+ hai."
            ),
            subtotal=subtotal,
            has_frozen=has_frozen,
        )

    # 3. Ambient minimum value
    if not has_frozen and subtotal < settings.MIN_AMBIENT_ORDER_INR:
        return OrderValidation(
            ok=False,
            reason="below_ambient_minimum",
            message=(
                f"Ambient orders ka minimum order value ₹{settings.MIN_AMBIENT_ORDER_INR} hai. "
                f"Aapka subtotal ₹{subtotal} hai. Thoda add karke phir try karein."
            ),
            subtotal=subtotal,
            has_frozen=has_frozen,
        )

    # 4. Delivery zone check
    if not _city_serviceable(city):
        return OrderValidation(
            ok=False,
            reason="city_not_serviceable",
            message=(
                f"Abhi hum {city or 'is location'} mein deliver nahi karte. "
                "Noida, Delhi NCR aur metro cities (Mumbai, Bengaluru, Chennai, "
                "Hyderabad, Kolkata, Pune, Ahmedabad, Jaipur, Lucknow) mein available hai. "
                "Aap apne nazdeeki city ke through manage kar sakte hain — sales se baat karein: 8800804580."
            ),
            subtotal=subtotal,
            has_frozen=has_frozen,
        )

    # 5. Delivery + packaging
    d = delivery_calculator.calculate(city, total_kg, has_frozen, order_value=subtotal)
    total = subtotal + d.packaging + d.shipping

    # 6. Max-order guard
    flagged_high_value = False
    if total > settings.MAX_ORDER_INR:
        return OrderValidation(
            ok=False,
            reason="above_max_order",
            message=(
                f"Itne bade order (₹{total:,}) ke liye humari team se baat karein: 8800804580. "
                "Custom contract terms aur payment options yahaan se decide hote hain."
            ),
            subtotal=subtotal,
            packaging=d.packaging,
            delivery=d.shipping,
            total=total,
            has_frozen=has_frozen,
            flagged_high_value=True,
        )
    if total > settings.MAX_ORDER_INR * 0.5:
        flagged_high_value = True   # warn-level flag, still accept

    # 7. LLM math sanity (override the LLM's total if it's off)
    if llm_total is not None and abs(int(llm_total) - total) > 10:
        # Not an error — but log via the result so the caller can replace
        return OrderValidation(
            ok=True,
            reason="calc_overridden",
            message=f"Recalculated: ₹{total:,} (was quoted ₹{llm_total:,}).",
            subtotal=subtotal,
            packaging=d.packaging,
            delivery=d.shipping,
            total=total,
            has_frozen=has_frozen,
            flagged_high_value=flagged_high_value,
        )

    return OrderValidation(
        ok=True,
        subtotal=subtotal,
        packaging=d.packaging,
        delivery=d.shipping,
        total=total,
        has_frozen=has_frozen,
        flagged_high_value=flagged_high_value,
    )


def estimate_kg_from_pieces(pieces: int, weight_per_piece_g: int) -> float:
    """Helper: convert a piece-count + per-piece weight to total kg."""
    if pieces <= 0 or weight_per_piece_g <= 0:
        return 0.0
    return (pieces * weight_per_piece_g) / 1000.0
