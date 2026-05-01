"""Website widget — REST endpoint.

Implements the same /api/chat contract the Next.js website widget already calls.
Single function ``handle_chat_request(request)`` that returns a serializable dict.

This handler is also reused by the Twilio WhatsApp channel — they share the
same brain (system prompt, lead extraction, guardrails, payment composition).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from pydantic import BaseModel, Field

from config import settings
from core import guardrails
from core.conversation import store
from core.intent_classifier import classify
from core.lead_tracker import extract_and_update, missing_fields
from core.llm_router import Message, generate
from core.logger import log_event
from middleware.sanitizer import sanitize_message, safe_page_url
from prompts.system_prompt import SYSTEM_PROMPT, build_user_context
from prompts.templates import (
    CONTACT_REPLY,
    GREETING_REPLY,
    NON_B2B_REPLY,
)
from services import crm, product_catalog, razorpay_links

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


# ─── Page-aware greetings ────────────────────────────────────────
def _greeting_template_for(page_url: str) -> str:
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


def _wrap(conv, reply: str, products=None, payment=None, actions=None) -> dict:
    return {
        "conversation_id": conv.conversation_id,
        "reply": reply,
        "products": products or [],
        "payment": payment,
        "actions": actions or [],
        "stage": conv.sales_stage,
        "lead_score": conv.lead_score,
    }


# ─── Main handler ────────────────────────────────────────────────
async def handle_chat_request(req: ChatRequest, channel: str = "website") -> dict:
    # 1. Sanitise + safe page URL
    sanitized = sanitize_message(req.message or "")
    user_msg = sanitized.text
    page_url = safe_page_url(req.metadata.page_url)

    # 2. Load / create conversation
    conv = store.get_or_create(
        conversation_id=req.conversation_id or None,
        channel=channel,  # type: ignore[arg-type]
    )

    if not user_msg:
        return _wrap(conv, "Tell me what you're sourcing — I'll match you to the right SKUs.", actions=_default_actions())

    # 3. Cooldown gate (after-abuse)
    if conv.is_in_cooldown():
        return _wrap(conv, guardrails.PERSISTENT_ABUSE_REPLY, actions=[
            {"label": "Open WhatsApp", "message": "OPEN_WHATSAPP"},
        ])

    # 4. Conversation cap
    if store.at_message_cap(conv):
        return _wrap(
            conv,
            "Bahut achhi baat hui! Ab aap seedha humari sales team se baat karein: 8800804580 — woh aapki exact requirement samajh ke best deal denge.",
            actions=[{"label": "Open WhatsApp", "message": "OPEN_WHATSAPP"}],
        )

    # 5. Reset / start over
    if store.maybe_reset(conv, user_msg):
        log_event("conversation_reset", conversation_id=conv.conversation_id)

    # 6. Guardrails — Layer 1 (prompt injection)
    if user_msg != "__greeting__" and guardrails.is_prompt_injection(user_msg):
        log_event("guardrail", level="WARNING", trigger="prompt_injection",
                  conversation_id=conv.conversation_id, msg_preview=user_msg[:120])
        conv.add("user", user_msg)
        conv.add("assistant", guardrails.INJECTION_REPLY)
        store.save(conv)
        return _wrap(conv, guardrails.INJECTION_REPLY, actions=[
            {"label": "Talk to sales", "message": "OPEN_WHATSAPP"},
        ])

    # 7. Guardrails — Layer 3 (threats / escalation / abuse)
    if guardrails.has_threat(user_msg):
        log_event("guardrail", level="WARNING", trigger="threat",
                  conversation_id=conv.conversation_id)
        conv.add("user", user_msg)
        conv.add("assistant", guardrails.THREAT_REPLY)
        store.save(conv)
        return _wrap(conv, guardrails.THREAT_REPLY, actions=[
            {"label": "Open WhatsApp", "message": "OPEN_WHATSAPP"},
        ])

    if guardrails.needs_escalation(user_msg):
        log_event("guardrail", level="WARNING", trigger="escalation",
                  conversation_id=conv.conversation_id)
        conv.add("user", user_msg)
        conv.add("assistant", guardrails.ESCALATION_REPLY)
        store.save(conv)
        crm.maybe_alert_sales(conv)
        return _wrap(conv, guardrails.ESCALATION_REPLY, actions=[
            {"label": "Open WhatsApp", "message": "OPEN_WHATSAPP"},
        ])

    if guardrails.has_profanity(user_msg):
        conv.abuse_count += 1
        if conv.abuse_count >= 3:
            import time as _t
            conv.cooldown_until = _t.time() + 1800   # 30 min
            log_event("guardrail", level="WARNING", trigger="persistent_abuse",
                      conversation_id=conv.conversation_id)
            conv.add("user", user_msg)
            conv.add("assistant", guardrails.PERSISTENT_ABUSE_REPLY)
            store.save(conv)
            return _wrap(conv, guardrails.PERSISTENT_ABUSE_REPLY, actions=[
                {"label": "Open WhatsApp", "message": "OPEN_WHATSAPP"},
            ])

    # 8. Classify intent
    intent = "GREETING"
    if user_msg != "__greeting__":
        intent = await classify(user_msg)
    conv.intents_seen.append(intent)

    # 9. Fast-shortcut intents
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

    # 10. Greeting (page-aware)
    if user_msg == "__greeting__":
        text = _greeting_template_for(page_url)
        store.save(conv)
        return _wrap(conv, text, actions=_greeting_actions(page_url))

    # 11. Add user turn + extract lead data + advance stage
    conv.add("user", user_msg)
    await extract_and_update(conv, user_msg)
    store.maybe_advance_stage(conv, intent)

    # 12. Loop detection
    recent = conv.recent_bot_replies(settings.LOOP_DETECT_WINDOW)
    if guardrails.is_loop(recent, settings.LOOP_DETECT_OVERLAP_THRESHOLD):
        log_event("loop_detected", conversation_id=conv.conversation_id)
        loop_msg = (
            "Lagta hai hum same topic pe aa rahe hain. Aap ek kaam karo — seedha humari sales team se baat karo: 8800804580. "
            "Woh aapki exact requirement samajh ke best deal denge!"
        )
        conv.sales_stage = 5
        conv.add("assistant", loop_msg)
        store.save(conv)
        crm.save_lead(conv)
        crm.maybe_alert_sales(conv)
        return _wrap(conv, loop_msg, actions=[
            {"label": "Open WhatsApp", "message": "OPEN_WHATSAPP"},
        ])

    # 13. Fetch relevant products
    products: list[dict] = []
    if intent in {"INQUIRY", "ORDER", "SAMPLE", "PAYMENT", "GENERAL"}:
        found = product_catalog.search(user_msg, limit=4)
        if not found and req.metadata.product_context and req.metadata.product_context.name:
            found = product_catalog.search(req.metadata.product_context.name, limit=4)
        products = [product_catalog.to_dict(p) for p in found]
        for p in found:
            if p.id not in conv.products_discussed:
                conv.products_discussed.append(p.id)

    # 14. Compose LLM prompt
    history = [{"role": m.role, "content": m.content} for m in conv.recent(settings.MAX_CONVERSATION_HISTORY)]
    user_ctx = build_user_context(
        sales_stage=conv.sales_stage,
        lead_data=conv.lead_data,
        missing_fields=missing_fields(conv.lead_data),
        products=products,
        intent=intent,
        history=history,
    )
    result = await generate(
        [Message(role="system", content=SYSTEM_PROMPT), Message(role="user", content=user_ctx)],
        task_type="customer_facing",
        temperature=0.55,
    )

    # 15. Fallback responder if all LLMs failed
    if result.error or not result.text:
        from channels.fallback_responder import respond as fallback_respond

        text, fb_products, fb_payment, fb_actions = fallback_respond(
            intent=intent, message=user_msg, conv_lead=conv.lead_data, products=products,
        )
        # Outbound guardrail still applies
        check = guardrails.validate_response(text)
        if not check.ok and check.rewritten:
            text = check.rewritten
        conv.add("assistant", text)
        store.save(conv)
        crm.save_lead(conv)
        crm.maybe_alert_sales(conv)
        return _wrap(
            conv, text,
            products=fb_products or _shape_products_for_widget(products),
            payment=fb_payment, actions=fb_actions or _default_actions(),
        )

    # 16. Outbound guardrail (price hallucination, prompt leak, forbidden promises)
    check = guardrails.validate_response(result.text)
    text = result.text if check.ok else (check.rewritten or result.text)

    # 17. Materialise [GENERATE_PAYMENT_LINK …] tokens
    text, payment = _materialise_payment(text, conv)

    # 18. Persist + alert
    conv.add("assistant", text)
    store.save(conv)
    crm.save_lead(conv)
    crm.maybe_alert_sales(conv)

    return _wrap(
        conv, text,
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
    if amount <= 0 or amount > settings.MAX_ORDER_INR:
        cleaned = PAYMENT_TOKEN.sub("", text).strip()
        if amount > settings.MAX_ORDER_INR:
            cleaned += "\n\nItne bade order ke liye sales team se baat karein: 8800804580"
        return cleaned, None
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
