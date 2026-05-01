"""Generate Razorpay Payment Page links pre-filled with order context.

The current implementation builds query-string links to the existing Payment
Page (`pl_SMfNZnTLY1GTcY`). When `RAZORPAY_KEY_ID/SECRET` are set, you can
upgrade to dynamically minted Payment Links via the Razorpay Orders API —
that's a one-function swap.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlencode

from config import settings


def page_link(amount: int, name: str, phone: Optional[str] = None, email: Optional[str] = None) -> str:
    """Compose a pre-filled Razorpay Payment Page URL.

    Razorpay Pages accept `amount` in **rupees** as a query param. We never
    invent contact details — only include what we actually collected.
    """
    base = f"https://pages.razorpay.com/{settings.RAZORPAY_PAYMENT_PAGE_ID}/view"
    params: dict[str, str] = {
        "amount": str(int(round(amount))),
        "name": name[:60],
    }
    if phone:
        # Razorpay expects 10-digit Indian phone
        digits = "".join(ch for ch in phone if ch.isdigit())
        if digits.startswith("91") and len(digits) > 10:
            digits = digits[-10:]
        if len(digits) == 10:
            params["contact"] = digits
    if email:
        params["email"] = email
    return f"{base}?{urlencode(params)}"


def to_card(
    amount: int,
    description: str,
    name: str,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    expires_in_minutes: int = 30,
) -> dict:
    """Format a payment card payload for the website widget."""
    return {
        "amount": int(round(amount)),
        "currency": "INR",
        "description": description[:200],
        "url": page_link(amount, name, phone, email),
        "expires_in_minutes": expires_in_minutes,
    }
