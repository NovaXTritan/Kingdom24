"""Twilio WhatsApp webhook handler.

Validates the X-Twilio-Signature, routes the inbound text into the same
chat brain the website uses, and replies with one or more outbound messages
(split at 1500 chars to respect WhatsApp limits).
"""

from __future__ import annotations

import logging
from typing import List

from fastapi import HTTPException, Request, Response

from channels.website import ChatRequest, Metadata, handle_chat_request
from config import settings

log = logging.getLogger("k24.whatsapp")
MAX_WA_MSG = 1500


async def handle_twilio_webhook(request: Request) -> Response:
    form = await request.form()
    body = (form.get("Body") or "").strip()
    from_phone = (form.get("From") or "").replace("whatsapp:", "").strip()

    # Optional signature validation
    if settings.WEBHOOK_SECRET and settings.twilio_enabled:
        if not _validate_twilio_signature(request, form):
            raise HTTPException(status_code=403, detail="invalid signature")

    # Map to the website-style chat request (we use the same brain)
    req = ChatRequest(
        conversation_id=f"wa_{from_phone}",
        message=body,
        metadata=Metadata(page_url="whatsapp", referrer="twilio", timestamp=""),
    )
    out = await handle_chat_request(req, channel="whatsapp")

    reply_text = out.get("reply", "")
    payment = out.get("payment")
    if payment and payment.get("url"):
        reply_text += f"\n\nPayment link: {payment['url']}\nAmount: ₹{payment['amount']}"

    chunks = _split(reply_text, MAX_WA_MSG)
    twiml = _twiml_response(chunks)
    return Response(content=twiml, media_type="application/xml")


def _split(text: str, max_len: int) -> List[str]:
    if len(text) <= max_len:
        return [text]
    out = []
    while text:
        if len(text) <= max_len:
            out.append(text)
            break
        # Split at last newline before limit, else hard split
        cut = text.rfind("\n", 0, max_len)
        if cut < int(max_len * 0.5):
            cut = max_len
        out.append(text[:cut])
        text = text[cut:].lstrip()
    return out


def _twiml_response(chunks: List[str]) -> str:
    body = "".join(
        f"<Message>{_xml_escape(c)}</Message>" for c in chunks if c.strip()
    ) or "<Message></Message>"
    return f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response>{body}</Response>"


def _xml_escape(text: str) -> str:
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _validate_twilio_signature(request: Request, form) -> bool:
    """Verify Twilio's X-Twilio-Signature header against the request body."""
    try:
        from twilio.request_validator import RequestValidator  # type: ignore

        signature = request.headers.get("X-Twilio-Signature", "")
        url = str(request.url)
        params = {k: v for k, v in form.items()}
        validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
        return validator.validate(url, params, signature)
    except Exception as e:  # noqa: BLE001
        log.warning("twilio_signature_validation_fail", extra={"error": str(e)})
        return False


async def handle_status_callback(request: Request) -> dict:
    form = await request.form()
    log.info(
        "twilio_status",
        extra={
            "sid": form.get("MessageSid"),
            "status": form.get("MessageStatus"),
            "to": form.get("To"),
        },
    )
    return {"ok": True}
