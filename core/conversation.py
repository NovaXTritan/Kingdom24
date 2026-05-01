"""Conversation state manager.

Holds active conversations in memory with an LRU evict policy + 2-hour idle TTL.
Every checkpoint is fsync'd to ``./storage/conversations.jsonl`` so a restart
re-hydrates from disk without losing the in-flight session.

Adds:
  - Per-conversation message cap (defended at sanitizer + here)
  - Active-conversation cap with LRU eviction
  - Stage-no-regression guard (with explicit "start over" reset)
  - Loop detection on bot replies (delegates to guardrails.is_loop)
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Literal, Optional

from config import STORAGE_DIR, settings

CONV_LOG = STORAGE_DIR / "conversations.jsonl"

Channel = Literal["whatsapp", "website"]
SalesStage = int  # 1..5

_RESET_RE = re.compile(r"\b(start over|restart|reset|nayi shuruaat|shuru se|fir se)\b", re.I)


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
    abuse_count: int = 0
    cooldown_until: float = 0.0       # epoch seconds; 0 = no cooldown
    closed: bool = False
    created_at: float = field(default_factory=time.time)
    last_message_at: float = field(default_factory=time.time)

    def add(self, role: Literal["user", "assistant"], content: str) -> None:
        self.messages.append(TurnMessage(role=role, content=content))
        self.last_message_at = time.time()

    def recent(self, limit: int) -> List[TurnMessage]:
        return self.messages[-limit:]

    def recent_bot_replies(self, n: int) -> List[str]:
        out = []
        for m in reversed(self.messages):
            if m.role == "assistant":
                out.append(m.content)
                if len(out) >= n:
                    break
        return list(reversed(out))

    def is_in_cooldown(self) -> bool:
        return time.time() < self.cooldown_until

    def message_count(self) -> int:
        return sum(1 for m in self.messages if m.role in {"user", "assistant"})

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


class ConversationStore:
    """In-memory LRU + JSONL persistence."""

    def __init__(self) -> None:
        self._mem: "OrderedDict[str, Conversation]" = OrderedDict()
        self._by_phone: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._loaded = False

    # ── Lifecycle ─────────────────────────────────────────────
    def get_or_create(
        self,
        conversation_id: Optional[str] = None,
        channel: Channel = "website",
        phone: str = "",
    ) -> Conversation:
        self._ensure_loaded()
        with self._lock:
            now = time.time()
            ttl = settings.CONVERSATION_TTL_SECONDS

            # Phone-based lookup (returning customer)
            if phone and phone in self._by_phone:
                cid = self._by_phone[phone]
                if cid in self._mem:
                    conv = self._mem[cid]
                    if now - conv.last_message_at < ttl:
                        self._mem.move_to_end(cid)
                        return conv

            # ID-based lookup
            cid = conversation_id or f"cv_{uuid.uuid4().hex[:12]}"
            if cid in self._mem:
                conv = self._mem[cid]
                if now - conv.last_message_at < ttl:
                    self._mem.move_to_end(cid)
                    return conv

            # New conversation
            conv = Conversation(conversation_id=cid, channel=channel, customer_phone=phone)
            self._mem[cid] = conv
            if phone:
                self._by_phone[phone] = cid
            self._evict_lru_if_needed()
            return conv

    def _evict_lru_if_needed(self) -> None:
        cap = settings.MAX_ACTIVE_CONVERSATIONS
        while len(self._mem) > cap:
            cid, conv = self._mem.popitem(last=False)
            if conv.customer_phone and self._by_phone.get(conv.customer_phone) == cid:
                self._by_phone.pop(conv.customer_phone, None)

    def save(self, conv: Conversation) -> None:
        try:
            with CONV_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(conv.to_dict(), ensure_ascii=False) + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except (OSError, AttributeError, ValueError):
                    # Fsync not supported on all platforms / streams — non-fatal
                    pass
        except OSError:
            pass

    # ── Restart re-hydration ──────────────────────────────────
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not CONV_LOG.exists():
            return
        cutoff = time.time() - settings.CONVERSATION_TTL_SECONDS
        try:
            latest_per_id: Dict[str, dict] = {}
            with CONV_LOG.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    cid = rec.get("conversation_id")
                    if not cid:
                        continue
                    if rec.get("last_message_at", 0) < cutoff:
                        continue
                    latest_per_id[cid] = rec
            for cid, rec in latest_per_id.items():
                conv = Conversation(
                    conversation_id=cid,
                    channel=rec.get("channel", "website"),
                    customer_phone=rec.get("customer_phone", ""),
                    customer_name=rec.get("customer_name", ""),
                    messages=[TurnMessage(**m) for m in rec.get("messages", [])],
                    sales_stage=int(rec.get("sales_stage", 1)),
                    lead_data=dict(rec.get("lead_data", {})),
                    lead_score=int(rec.get("lead_score", 0)),
                    products_discussed=list(rec.get("products_discussed", [])),
                    intents_seen=list(rec.get("intents_seen", [])),
                    abuse_count=int(rec.get("abuse_count", 0)),
                    cooldown_until=float(rec.get("cooldown_until", 0)),
                    closed=bool(rec.get("closed", False)),
                    created_at=float(rec.get("created_at", time.time())),
                    last_message_at=float(rec.get("last_message_at", time.time())),
                )
                self._mem[cid] = conv
                if conv.customer_phone:
                    self._by_phone[conv.customer_phone] = cid
        except OSError:
            pass

    # ── Stage progression (forward-only) ──────────────────────
    def maybe_advance_stage(self, conv: Conversation, intent: str) -> None:
        prior = conv.sales_stage
        if conv.sales_stage == 1 and (
            conv.lead_data.get("business_type") or conv.lead_data.get("city")
        ):
            conv.sales_stage = 2
        if conv.sales_stage == 2 and intent in {"INQUIRY", "ORDER"}:
            conv.sales_stage = 3
        if conv.sales_stage == 3 and intent in {"PAYMENT", "COMPLAINT"}:
            conv.sales_stage = 4
        if intent in {"ORDER", "SAMPLE"} or conv.lead_score >= settings.LEAD_ALERT_THRESHOLD:
            conv.sales_stage = max(conv.sales_stage, 5)
        # Forward-only — never let stage decrease unless explicit reset
        if conv.sales_stage < prior:
            conv.sales_stage = prior

    def maybe_reset(self, conv: Conversation, message: str) -> bool:
        if _RESET_RE.search(message or ""):
            conv.sales_stage = 1
            conv.products_discussed = []
            conv.closed = False
            return True
        return False

    # ── Cap enforcement ───────────────────────────────────────
    def at_message_cap(self, conv: Conversation) -> bool:
        return conv.message_count() >= settings.MAX_MESSAGES_PER_CONVERSATION

    # ── Retrieval / stats ─────────────────────────────────────
    def by_id(self, cid: str) -> Optional[Conversation]:
        self._ensure_loaded()
        return self._mem.get(cid)

    def by_phone(self, phone: str) -> Optional[Conversation]:
        self._ensure_loaded()
        cid = self._by_phone.get(phone)
        return self._mem.get(cid) if cid else None

    def stats(self) -> dict:
        now = time.time()
        ttl = settings.CONVERSATION_TTL_SECONDS
        active = [c for c in self._mem.values() if now - c.last_message_at < ttl]
        return {
            "active_conversations": len(active),
            "total_in_memory": len(self._mem),
            "channels": {
                "website": sum(1 for c in active if c.channel == "website"),
                "whatsapp": sum(1 for c in active if c.channel == "whatsapp"),
            },
            "max_active": settings.MAX_ACTIVE_CONVERSATIONS,
        }

    def reset_for_tests(self) -> None:
        with self._lock:
            self._mem.clear()
            self._by_phone.clear()
            self._loaded = True


store = ConversationStore()
