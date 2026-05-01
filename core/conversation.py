"""Conversation state manager.

Holds active conversations in memory with a 2-hour idle TTL, persists each
message to `./storage/conversations.jsonl` (append-only). This keeps the
service stateless from the OS perspective — restart and active conversations
re-hydrate from disk for any phone number that messages within the TTL.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Optional

from config import STORAGE_DIR, settings

CONV_LOG = STORAGE_DIR / "conversations.jsonl"
TTL_SECONDS = 60 * 60 * 2  # 2 hours

Channel = Literal["whatsapp", "website"]
SalesStage = int  # 1..5


@dataclass
class TurnMessage:
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class Conversation:
    conversation_id: str
    channel: Channel
    customer_phone: str = ""
    customer_name: str = ""
    messages: List[TurnMessage] = field(default_factory=list)
    sales_stage: SalesStage = 1
    lead_data: Dict[str, str] = field(default_factory=dict)
    lead_score: int = 0
    products_discussed: List[str] = field(default_factory=list)
    intents_seen: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_message_at: float = field(default_factory=time.time)

    def add(self, role: Literal["user", "assistant"], content: str) -> None:
        self.messages.append(TurnMessage(role=role, content=content))
        self.last_message_at = time.time()

    def recent(self, limit: int) -> List[TurnMessage]:
        return self.messages[-limit:]

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


class ConversationStore:
    """In-memory + JSONL append-only persistence."""

    def __init__(self) -> None:
        self._mem: Dict[str, Conversation] = {}
        self._by_phone: Dict[str, str] = {}
        self._lock = threading.Lock()

    # ── Lifecycle ─────────────────────────────────────────────
    def get_or_create(
        self,
        conversation_id: Optional[str] = None,
        channel: Channel = "website",
        phone: str = "",
    ) -> Conversation:
        with self._lock:
            if phone and phone in self._by_phone:
                cid = self._by_phone[phone]
                if cid in self._mem:
                    conv = self._mem[cid]
                    if time.time() - conv.last_message_at < TTL_SECONDS:
                        return conv

            cid = conversation_id or f"cv_{uuid.uuid4().hex[:12]}"
            if cid in self._mem:
                conv = self._mem[cid]
                if time.time() - conv.last_message_at < TTL_SECONDS:
                    return conv

            conv = Conversation(conversation_id=cid, channel=channel, customer_phone=phone)
            self._mem[cid] = conv
            if phone:
                self._by_phone[phone] = cid
            return conv

    def save(self, conv: Conversation) -> None:
        # Persist as one JSON line per checkpoint — easy to grep / replay.
        try:
            with CONV_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(conv.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ── Sales-stage progression ───────────────────────────────
    def maybe_advance_stage(self, conv: Conversation, intent: str) -> None:
        # 1 → 2: any business signal collected
        if conv.sales_stage == 1 and (
            conv.lead_data.get("business_type") or conv.lead_data.get("city")
        ):
            conv.sales_stage = 2
        # 2 → 3: pain point or product inquiry
        if conv.sales_stage == 2 and intent in {"INQUIRY", "ORDER"}:
            conv.sales_stage = 3
        # 3 → 4: pricing / objection
        if conv.sales_stage == 3 and intent in {"PAYMENT", "COMPLAINT"}:
            conv.sales_stage = 4
        # → 5: clear close intent
        if intent in {"ORDER", "SAMPLE"} or conv.lead_score >= settings.LEAD_ALERT_THRESHOLD:
            conv.sales_stage = max(conv.sales_stage, 5)

    # ── Retrieval ─────────────────────────────────────────────
    def by_id(self, cid: str) -> Optional[Conversation]:
        return self._mem.get(cid)

    def by_phone(self, phone: str) -> Optional[Conversation]:
        cid = self._by_phone.get(phone)
        return self._mem.get(cid) if cid else None

    def stats(self) -> dict:
        active = [c for c in self._mem.values() if time.time() - c.last_message_at < TTL_SECONDS]
        return {
            "active_conversations": len(active),
            "total_in_memory": len(self._mem),
            "channels": {
                "website": sum(1 for c in active if c.channel == "website"),
                "whatsapp": sum(1 for c in active if c.channel == "whatsapp"),
            },
        }


store = ConversationStore()
