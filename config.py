"""Settings, constants and environment loading.

All providers are optional. The router cascades through them in priority order,
falling back to a deterministic keyword-based responder if every provider is
unavailable. This means the service runs end-to-end without any API key — wire
keys in one at a time as you provision them.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STORAGE_DIR = ROOT / "storage"
LOG_DIR = STORAGE_DIR / "logs"
BACKUP_DIR = STORAGE_DIR / "backups"
STORAGE_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
BACKUP_DIR.mkdir(exist_ok=True)

# Path to the website's authoritative product catalogue. Falls back to local
# data/products.json if absent.
WEBSITE_PRODUCTS_JSON = (ROOT.parent / "kingdom24-web" / "data" / "products.json").resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=True)

    # ── App ────────────────────────────────────────────────────
    ENV: str = "development"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    ALLOWED_ORIGINS: str = (
        "https://www.kingdom24.in,https://kingdom24.in,http://localhost:3000,http://localhost:8000"
    )

    # ── LLM providers (all optional) ───────────────────────────
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    # ── LLM models ────────────────────────────────────────────
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GROQ_MODEL_FAST: str = "llama-3.1-8b-instant"
    GROQ_MODEL_SMART: str = "llama-3.3-70b-versatile"
    OLLAMA_MODEL: str = "llama3.1:8b"

    # ── LLM timeouts (seconds) ────────────────────────────────
    GEMINI_TIMEOUT_S: float = 10.0
    GROQ_TIMEOUT_S: float = 5.0
    OLLAMA_TIMEOUT_S: float = 30.0

    # ── Circuit breaker ───────────────────────────────────────
    CIRCUIT_FAILURE_THRESHOLD: int = 3
    CIRCUIT_OPEN_SECONDS: int = 60

    # ── Twilio (optional) ─────────────────────────────────────
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_WHATSAPP_NUMBER: str = ""
    WEBHOOK_SECRET: str = ""

    # ── Storage (optional) ────────────────────────────────────
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""

    # ── Razorpay ──────────────────────────────────────────────
    RAZORPAY_PAYMENT_PAGE_ID: str = "pl_SMfNZnTLY1GTcY"
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""

    # ── Sales ─────────────────────────────────────────────────
    SALES_TEAM_PHONE: str = "918800804580"
    ADMIN_PHONE: str = ""             # alerts go here if set
    LEAD_ALERT_THRESHOLD: int = 70

    # ── Auth ──────────────────────────────────────────────────
    STATS_API_KEY: str = ""           # blank → /api/stats requires NO auth in dev

    # ── PII ───────────────────────────────────────────────────
    PII_HASH_SALT: str = ""           # if blank, generated at startup (volatile)

    # ── Quota limits ──────────────────────────────────────────
    GEMINI_DAILY_LIMIT: int = 1500
    GEMINI_DAILY_HARD_STOP: int = 1450     # 97% — stop before exhaustion
    GEMINI_DAILY_WARN: int = 1200          # 80% — log warning
    GROQ_RPM_LIMIT: int = 30
    GROQ_RPM_HARD_STOP: int = 29
    GROQ_RPM_WARN: int = 25

    # ── Conversation limits ───────────────────────────────────
    MAX_CONVERSATION_HISTORY: int = 20
    MAX_REPLY_TOKENS: int = 2000   # Gemini 2.5 uses thinking tokens that share this budget
    MAX_MESSAGES_PER_CONVERSATION: int = 100
    MAX_MESSAGE_LENGTH: int = 2000
    MAX_ACTIVE_CONVERSATIONS: int = 5000
    CONVERSATION_TTL_SECONDS: int = 7200    # 2 hours
    LOOP_DETECT_OVERLAP_THRESHOLD: float = 0.6
    LOOP_DETECT_WINDOW: int = 3
    LOOP_DETECT_STAGE_STAGNATION: int = 5

    # ── Order validation ──────────────────────────────────────
    MAX_ORDER_INR: int = 500_000
    MIN_AMBIENT_ORDER_INR: int = 500
    DEFAULT_FROZEN_MOQ_KG: int = 30
    DEFAULT_AMBIENT_MOQ_KG: int = 10

    # ── Lead scoring (mutable via env) ────────────────────────
    LEAD_W_BUSINESS_TYPE: int = 15
    LEAD_W_OUTLET_NAME: int = 15
    LEAD_W_CITY: int = 10
    LEAD_W_PHONE: int = 20
    LEAD_W_VOLUME: int = 15
    LEAD_W_STORAGE: int = 10
    LEAD_W_DECISION_MAKER: int = 15

    # ── Lead decay ────────────────────────────────────────────
    LEAD_DECAY_7D: float = 0.8
    LEAD_DECAY_30D: float = 0.5
    LEAD_COLD_DAYS: int = 90

    # ── Rate limits ───────────────────────────────────────────
    RL_CHAT_PER_IP_MIN: int = 30
    RL_CHAT_PER_IP_HOUR: int = 200
    RL_CHAT_PER_CONV_HOUR: int = 60
    RL_CHAT_GLOBAL_DAILY: int = 1400
    RL_WHATSAPP_PER_PHONE_MIN: int = 30
    RL_STATS_PER_IP_MIN: int = 10

    # ── Body / payload ────────────────────────────────────────
    MAX_REQUEST_BYTES: int = 10 * 1024     # 10 KB

    # ── Catalog hot reload ────────────────────────────────────
    CATALOG_RELOAD_POLL_SECONDS: int = 300   # 5 min

    # ── Health check cache ────────────────────────────────────
    HEALTH_CACHE_SECONDS: int = 30

    # ── Alerting ──────────────────────────────────────────────
    ALERT_DEDUPE_SECONDS: int = 3600
    ALERT_ERROR_BURST_COUNT: int = 5
    ALERT_ERROR_BURST_WINDOW_S: int = 300
    ALERT_DISK_FREE_MB_FLOOR: int = 500

    # ── Catalog source override (mostly for tests) ────────────
    PRODUCTS_JSON_PATH: str = ""              # blank → auto-resolve

    # ── Derived helpers ───────────────────────────────────────
    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.GEMINI_API_KEY)

    @property
    def groq_enabled(self) -> bool:
        return bool(self.GROQ_API_KEY)

    @property
    def ollama_enabled(self) -> bool:
        return bool(self.OLLAMA_BASE_URL)

    @property
    def twilio_enabled(self) -> bool:
        return bool(
            self.TWILIO_ACCOUNT_SID and self.TWILIO_AUTH_TOKEN and self.TWILIO_WHATSAPP_NUMBER
        )

    @property
    def supabase_enabled(self) -> bool:
        return bool(self.SUPABASE_URL and self.SUPABASE_KEY)

    @property
    def lead_score_weights(self) -> dict:
        return {
            "business_type": self.LEAD_W_BUSINESS_TYPE,
            "outlet_name": self.LEAD_W_OUTLET_NAME,
            "city": self.LEAD_W_CITY,
            "phone": self.LEAD_W_PHONE,
            "volume": self.LEAD_W_VOLUME,
            "storage": self.LEAD_W_STORAGE,
            "decision_maker": self.LEAD_W_DECISION_MAKER,
        }


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if not s.PII_HASH_SALT:
        # Volatile salt — fine for the lifetime of one process. For real
        # cross-restart deletion-by-hash, set PII_HASH_SALT in .env.
        s.PII_HASH_SALT = secrets.token_hex(16)
    return s


settings = get_settings()
