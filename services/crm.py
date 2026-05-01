"""Lead persistence + sales-team alerts.

PII protection:
  - JSONL log stores `phone_hash` (SHA-256 + salt), never plaintext
  - In-process conversation memory keeps plaintext (needed for Razorpay link)
  - Sales-team WhatsApp alert sends plaintext (they need it to call)
  - DPDP delete: removes every record whose phone_hash matches the input

Storage: tries Supabase if credentials present, else appends to
`./storage/leads.jsonl`. The read path returns the latest record per phone hash.
"""

from __future__ import annotations

import json
import logging
import time
from typing import List, Optional

from config import STORAGE_DIR, settings
from core.conversation import Conversation
from core.logger import alert_on_error, hash_phone, mask_phone

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
        client.table("chatbot_leads").upsert(payload, on_conflict="conversation_id").execute()
    except Exception as e:  # noqa: BLE001
        log.warning("crm_supabase_fail", extra={"error": str(e)})


def _redact_phones(text: str, phones_to_hide: list[str]) -> str:
    if not text:
        return ""
    out = text
    for ph in phones_to_hide:
        if ph and len(ph) >= 6:
            out = out.replace(ph, "[PHONE_REDACTED]")
    return out


def save_lead(conv: Conversation) -> None:
    plain_phone = conv.lead_data.get("phone") or conv.customer_phone or ""
    payload = {
        "conversation_id": conv.conversation_id,
        "channel": conv.channel,
        "phone_hash": hash_phone(plain_phone) if plain_phone else None,
        "phone_masked": mask_phone(plain_phone) if plain_phone else None,
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
    """Send a WhatsApp alert if score crosses threshold for the first time."""
    if conv.lead_score < settings.LEAD_ALERT_THRESHOLD:
        return False
    marker = STORAGE_DIR / f"alerted_{conv.conversation_id}.flag"
    if marker.exists():
        return False
    marker.touch()

    text = _format_alert(conv)
    sent = False
    if settings.twilio_enabled:
        sent = _send_twilio(text)

    try:
        with ALERTS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "conversation_id": conv.conversation_id,
                "lead_score": conv.lead_score,
                "phone_hash": hash_phone(conv.lead_data.get("phone", "")),
                "text": text,
                "sent": sent,
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass
    log.info("crm_alert", extra={"sent": sent, "score": conv.lead_score})
    return sent or True


def _format_alert(conv: Conversation) -> str:
    """Sales-team alert — KEEPS plaintext phone (they need to call). Recent
    messages are redacted before logging."""
    plain_phone = conv.lead_data.get("phone") or conv.customer_phone or "—"
    last_msgs = "\n".join(
        f"  {m.role[:1].upper()}: {_redact_phones(m.content, [plain_phone])[:140]}"
        for m in conv.recent(3)
    )
    ld = conv.lead_data
    return (
        "Hot lead from chatbot\n"
        f"Score {conv.lead_score}/100 · stage {conv.sales_stage}/5 · {conv.channel}\n"
        f"Business: {ld.get('outlet_name') or '—'} ({ld.get('business_type') or '?'})\n"
        f"City: {ld.get('city') or '—'}\n"
        f"Volume: {ld.get('volume') or '—'}\n"
        f"Storage: {ld.get('storage') or '—'}\n"
        f"Phone: {plain_phone}\n"
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
        alert_on_error("twilio_send_fail", {"error": str(e)})
        return False


def get_lead_by_phone_hash(phone_hash: str) -> Optional[dict]:
    if not phone_hash:
        return None
    try:
        last: Optional[dict] = None
        with LEADS_LOG.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("phone_hash") == phone_hash:
                    last = rec
        return last
    except FileNotFoundError:
        return None


def delete_by_phone(plain_phone: str) -> dict:
    """DPDP-style 'right to be forgotten' — wipe leads + conversations + alerts
    matching the given phone number.

    Returns a summary {leads_removed, conversations_removed, alerts_removed}.
    """
    target_hash = hash_phone(plain_phone)
    if not target_hash:
        return {"leads_removed": 0, "conversations_removed": 0, "alerts_removed": 0}

    leads_removed = _filter_jsonl(LEADS_LOG, lambda r: r.get("phone_hash") == target_hash)
    alerts_removed = _filter_jsonl(ALERTS_LOG, lambda r: r.get("phone_hash") == target_hash)

    # Conversations file stores plaintext phone in customer_phone — drop matching
    conv_file = STORAGE_DIR / "conversations.jsonl"
    convs_removed = _filter_jsonl(
        conv_file,
        lambda r: r.get("customer_phone") == plain_phone or hash_phone(r.get("customer_phone", "")) == target_hash,
    )

    # Drop the in-memory conversation too
    from core.conversation import store as _store

    cv = _store.by_phone(plain_phone)
    if cv:
        _store._mem.pop(cv.conversation_id, None)  # noqa: SLF001
        _store._by_phone.pop(plain_phone, None)    # noqa: SLF001

    return {
        "leads_removed": leads_removed,
        "conversations_removed": convs_removed,
        "alerts_removed": alerts_removed,
        "phone_masked": mask_phone(plain_phone),
    }


def _filter_jsonl(path, predicate) -> int:
    """Rewrite a JSONL file dropping records that match `predicate`. Returns count removed."""
    if not path.exists():
        return 0
    removed = 0
    kept_lines: List[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                kept_lines.append(line)
                continue
            if predicate(rec):
                removed += 1
            else:
                kept_lines.append(line)
    with path.open("w", encoding="utf-8") as f:
        for line in kept_lines:
            f.write(line + "\n")
    return removed


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


# Backwards-compat wrapper
def get_lead_by_phone(phone: str) -> Optional[dict]:
    return get_lead_by_phone_hash(hash_phone(phone))
