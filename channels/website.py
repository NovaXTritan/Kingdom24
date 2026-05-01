"""Website widget — REST endpoint.

Implements the same /api/chat contract the Next.js website widget already calls.
Single function `handle_chat_request(request)` that returns a serializable dict.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

from pydantic import BaseModel, Field

from config import settings
from core.conversation import store
from core.intent_classifier import classify
from core.lead_tracker import extract_and_update, missing_fields
from core.llm_router import Message, generate
from prompts.system_prompt import SYSTEM_PROMPT, build_user_context
from prompts.templates import (
    CONTACT_REPLY,
    GREETING_REPLY,
    NON_B2B_REPLY,
    NO_PROVIDER_FALLBACK,
)
from services import product_catalog, razorpay_links, crm

log = logging.getLogger("k24.website")

PAYMENT_TOKEN = re.compile(
    r"\[GENERATE_PAYMENT_LINK\s+amount=([\d,]+)\s+name=([^\]\s][^\]]*?)(?:\s+phone=(\d{8,15}))?\s*\]",
    re.IGNORECASE,
)


class ProductContext(BaseModel):
    slug: Optional[str] = None
    name: Optional[str] = None
    price: Optional[float] = None
    sku: Optional[str] = None


class Metadata(BaseModel):
    page_url: str = "/"
    referrer: str = ""
    product_context: Optional[ProductContext] = None
    timestamp: str = ""


class ChatRequest(BaseModel):
    conversation_id: str = ""
    message: str
    metadata: Metadata = Field(default_factory=Metadata)


def _greeting_template_for(page_url: str) -> str:
    """Page-aware deterministic greeting (used when LLM unavailable)."""
    url = (page_url or "/").lower()
    if url.startswith("/products/"):
        return ("I see you're looking at this product. Want bulk pricing for your monthly volume, "
                "or shall I share related SKUs that pair well with it?")
    if url.startswith("/categories/"):
        return ("Browsing this category? I can recommend the top sellers for your kitchen format "
                "and share bulk pricing.")
    if url.startswith("/contact"):
        return "Hi — happy to help here directly. Tell me about your business and what you're sourcing."
    if url.startswith("/price-list"):
        return ("I can pull SKU pricing on demand. Tell me what you're sourcing and the volume — "
                "I'll match you to the right tier.")
    return GREETING_REPLY


async def handle_chat_request(req: ChatRequest, channel: str = "website") -> dict:
    # 1. Load / create conversation
    conv = store.get_or_create(
        conversation_id=req.conversation_id or None,
        channel=channel,  # type: ignore[arg-type]
    )

    user_msg = (req.message or "").strip()
    if not user_msg:
        return _wrap(conv, "Tell me what you're sourcing — I'll match you to the right SKUs.", actions=_default_actions())

    # 2. Classify intent
    intent = "GREETING"
    if user_msg != "__greeting__":
        intent = await classify(user_msg)
    conv.intents_seen.append(intent)

    # 3. Fast-shortcut intents
    if intent == "NON_B2B":
        return _wrap(conv, NON_B2B_REPLY, actions=[
            {"label": "I run a business", "message": "I run a food business — let me share details"},
            {"label": "Open WhatsApp", "message": "OPEN_WHATSAPP"},
        ])

    if intent == "CONTACT":
        return _wrap(conv, CONTACT_REPLY, actions=[
            {"label": "Open WhatsApp", "message": "OPEN_WHATSAPP"},
            {"label": "Send a quote enquiry", "message": "I want a bulk quote"},
        ])

    if user_msg == "__greeting__":
        text = _greeting_template_for(req.metadata.page_url)
        store.save(conv)
        return _wrap(conv, text, actions=_greeting_actions(req.metadata.page_url))

    # 4. Add user turn, extract lead data
    conv.add("user", user_msg)
    await extract_and_update(conv, user_msg)
    store.maybe_advance_stage(conv, intent)

    # 5. Fetch relevant products if intent looks product-y
    products: list[dict] = []
    if intent in {"INQUIRY", "ORDER", "SAMPLE", "PAYMENT", "GENERAL"}:
        found = product_catalog.search(user_msg, limit=4)
        if not found and req.metadata.product_context and req.metadata.product_context.name:
            found = product_catalog.search(req.metadata.product_context.name, limit=4)
        products = [product_catalog.to_dict(p) for p in found]
        for p in found:
            if p.id not in conv.products_discussed:
                conv.products_discussed.append(p.id)

    # 6. Compose LLM prompt
    history = [
        {"role": m.role, "content": m.content} for m in conv.recent(settings.MAX_CONVERSATION_HISTORY)
    ]
    user_ctx = build_user_context(
        sales_stage=conv.sales_stage,
        lead_data=conv.lead_data,
        missing_fields=missing_fields(conv.lead_data),
        products=products,
        intent=intent,
        history=history,
    )

    result = await generate(
        [
            Message(role="system", content=SYSTEM_PROMPT),
            Message(role="user", content=user_ctx),
        ],
        task_type="customer_facing",
        temperature=0.55,
    )

    if result.error or not result.text:
        # Fallback when no LLM is available — deterministic responder
        from channels.fallback_responder import respond as fallback_respond
        text, fb_products, fb_payment, fb_actions = fallback_respond(
            intent=intent,
            message=user_msg,
            conv_lead=conv.lead_data,
            products=products,
        )
        conv.add("assistant", text)
        store.save(conv)
        crm.save_lead(conv)
        crm.maybe_alert_sales(conv)
        return _wrap(
            conv,
            text,
            products=fb_products or _shape_products_for_widget(products),
            payment=fb_payment,
            actions=fb_actions or _default_actions(),
        )

    # 7. Resolve [GENERATE_PAYMENT_LINK ...] tokens to a real card
    text, payment = _materialise_payment(result.text, conv)
    conv.add("assistant", text)
    store.save(conv)
    crm.save_lead(conv)
    crm.maybe_alert_sales(conv)

    return _wrap(
        conv,
        text,
        products=_shape_products_for_widget(products),
        payment=payment,
        actions=_default_actions(),
    )


def _materialise_payment(text: str, conv) -> tuple[str, Optional[dict]]:
    m = PAYMENT_TOKEN.search(text)
    if not m:
        return text, None
    amount_raw, name_raw, phone_raw = m.group(1), m.group(2), m.group(3)
    amount = int(amount_raw.replace(",", "").strip() or "0")
    if amount <= 0:
        return PAYMENT_TOKEN.sub("", text).strip(), None
    name = (name_raw or conv.lead_data.get("outlet_name") or "Order").strip()[:60]
    phone = (phone_raw or conv.lead_data.get("phone") or "").strip()
    payment = razorpay_links.to_card(
        amount=amount,
        description=f"Trial order from chat ({conv.conversation_id[:10]})",
        name=name,
        phone=phone or None,
        email=conv.lead_data.get("email"),
    )
    cleaned = PAYMENT_TOKEN.sub("", text).strip()
    return cleaned, payment


def _shape_products_for_widget(products: list[dict]) -> list[dict]:
    out = []
    for p in products:
        out.append({
            "slug": p["id"],
            "name": p["name"],
            "category": p["category"],
            "image": p.get("image_url") or "",
            "price": p["price_per_kg"] or 0,
            "pack": ", ".join(p["pack_sizes"][:2]),
            "moq": f"MOQ {p['moq']}{p['moq_unit']}",
        })
    return out


def _default_actions() -> list[dict]:
    return [
        {"label": "Get a quote",        "message": "I want a bulk quote for 100 kg per month"},
        {"label": "Browse bestsellers", "message": "Show me your bestsellers"},
        {"label": "Talk to sales",      "message": "OPEN_WHATSAPP"},
    ]


def _greeting_actions(page_url: str) -> list[dict]:
    if (page_url or "/").startswith("/products/"):
        return [
            {"label": "Get bulk pricing",     "message": "I want bulk pricing for this product"},
            {"label": "Related products",     "message": "Show me related products"},
            {"label": "Request a sample",     "message": "I want to order a sample"},
        ]
    return [
        {"label": "I run a hotel",        "message": "I run a hotel — what should I start with?"},
        {"label": "I run a restaurant",   "message": "I run a restaurant — recommend bestsellers"},
        {"label": "Cloud kitchen",        "message": "I operate a cloud kitchen"},
        {"label": "Just browsing",        "message": "Show me your bestsellers"},
    ]


def _wrap(conv, reply: str, products: Optional[list] = None, payment: Optional[dict] = None, actions: Optional[list] = None) -> dict:
    return {
        "conversation_id": conv.conversation_id,
        "reply": reply,
        "products": products or [],
        "payment": payment,
        "actions": actions or [],
        "stage": conv.sales_stage,
        "lead_score": conv.lead_score,
    }
