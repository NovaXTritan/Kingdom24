"""Lead persistence + sales-team alerts.

Storage: tries Supabase if credentials present, else appends to
`./storage/leads.jsonl`. Both are write-mostly stores; the read path returns
the latest record per phone for returning customers.

Sales alert: WhatsApp message to `SALES_TEAM_PHONE` via Twilio when the lead
score crosses the threshold for the first time.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from typing import Optional

from config import STORAGE_DIR, settings
from core.conversation import Conversation

log = logging.getLogger("k24.crm")
LEADS_LOG = STORAGE_DIR / "leads.jsonl"
ALERTS_LOG = STORAGE_DIR / "alerts.jsonl"


def _persist_local(payload: dict) -> None:
    try:
        with LEADS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("crm_local_write_fail", extra={"error": str(e)})


def _persist_supabase(payload: dict) -> None:
    if not settings.supabase_enabled:
        return
    try:
        from supabase import create_client  # type: ignore

        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        # Schema is documented in CLAUDE.md — table "chatbot_leads"
        client.table("chatbot_leads").upsert(payload, on_conflict="conversation_id").execute()
    except Exception as e:  # noqa: BLE001
        log.warning("crm_supabase_fail", extra={"error": str(e)})


def save_lead(conv: Conversation) -> None:
    payload = {
        "conversation_id": conv.conversation_id,
        "channel": conv.channel,
        "phone": conv.lead_data.get("phone") or conv.customer_phone,
        "outlet_name": conv.lead_data.get("outlet_name"),
        "business_type": conv.lead_data.get("business_type"),
        "city": conv.lead_data.get("city"),
        "volume": conv.lead_data.get("volume"),
        "storage": conv.lead_data.get("storage"),
        "decision_maker": bool(conv.lead_data.get("decision_maker")),
        "email": conv.lead_data.get("email"),
        "lead_score": conv.lead_score,
        "sales_stage": conv.sales_stage,
        "products_discussed": conv.products_discussed,
        "intents_seen": conv.intents_seen,
        "updated_at": time.time(),
    }
    _persist_local(payload)
    _persist_supabase(payload)


def maybe_alert_sales(conv: Conversation) -> bool:
    """Send a WhatsApp alert if score crosses threshold for the first time.

    Returns True if an alert was sent (or queued)."""
    if conv.lead_score < settings.LEAD_ALERT_THRESHOLD:
        return False
    # De-dupe per conversation: only alert once
    marker = STORAGE_DIR / f"alerted_{conv.conversation_id}.flag"
    if marker.exists():
        return False
    marker.touch()

    text = _format_alert(conv)
    sent = False
    if settings.twilio_enabled:
        sent = _send_twilio(text)

    # Always log the alert payload locally
    try:
        with ALERTS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "conversation_id": conv.conversation_id,
                "lead_score": conv.lead_score,
                "text": text,
                "sent": sent,
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass
    log.info("crm_alert", extra={"sent": sent, "score": conv.lead_score})
    return sent or True


def _format_alert(conv: Conversation) -> str:
    last_msgs = "\n".join(
        f"  {m.role[:1].upper()}: {m.content[:140]}" for m in conv.recent(3)
    )
    ld = conv.lead_data
    return (
        "Hot lead from chatbot\n"
        f"Score {conv.lead_score}/100 · stage {conv.sales_stage}/5 · {conv.channel}\n"
        f"Business: {ld.get('outlet_name') or '—'} ({ld.get('business_type') or '?'})\n"
        f"City: {ld.get('city') or '—'}\n"
        f"Volume: {ld.get('volume') or '—'}\n"
        f"Storage: {ld.get('storage') or '—'}\n"
        f"Phone: {ld.get('phone') or conv.customer_phone or '—'}\n"
        f"Products: {', '.join(conv.products_discussed[-5:]) or '—'}\n"
        f"Recent:\n{last_msgs}"
    )


def _send_twilio(text: str) -> bool:
    try:
        from twilio.rest import Client  # type: ignore

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        client.messages.create(
            body=text[:1500],
            from_=settings.TWILIO_WHATSAPP_NUMBER,
            to=f"whatsapp:+{settings.SALES_TEAM_PHONE}",
        )
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("twilio_send_fail", extra={"error": str(e)})
        return False


def get_lead_by_phone(phone: str) -> Optional[dict]:
    """Return the most recent lead snapshot for a phone number (local store)."""
    if not phone:
        return None
    try:
        last: Optional[dict] = None
        with LEADS_LOG.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("phone") == phone:
                    last = rec
        return last
    except FileNotFoundError:
        return None


def daily_stats() -> dict:
    today_start = time.time() - 86400
    convs = orders = qualified = 0
    try:
        with LEADS_LOG.open("r", encoding="utf-8") as f:
            seen_conv = set()
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("updated_at", 0) < today_start:
                    continue
                if rec["conversation_id"] not in seen_conv:
                    convs += 1
                    seen_conv.add(rec["conversation_id"])
                if rec.get("lead_score", 0) >= settings.LEAD_ALERT_THRESHOLD:
                    qualified += 1
                if rec.get("sales_stage", 0) >= 5:
                    orders += 1
    except FileNotFoundError:
        pass
    return {
        "conversations_today": convs,
        "qualified_leads_today": qualified,
        "orders_today": orders,
    }
