"""Settings, constants and environment loading.

All providers are optional. The router cascades through them in priority order,
falling back to a deterministic keyword-based responder if every provider is
unavailable. This means the service runs end-to-end without any API key — wire
keys in one at a time as you provision them.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STORAGE_DIR = ROOT / "storage"
STORAGE_DIR.mkdir(exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=True)

    # App
    ENV: str = "development"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    ALLOWED_ORIGINS: str = (
        "https://www.kingdom24.in,https://kingdom24.in,http://localhost:3000,http://localhost:8000"
    )

    # LLM providers (all optional)
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    # LLM models
    GEMINI_MODEL: str = "gemini-2.0-flash-exp"
    GROQ_MODEL_FAST: str = "llama-3.1-8b-instant"
    GROQ_MODEL_SMART: str = "llama-3.3-70b-versatile"
    OLLAMA_MODEL: str = "llama3.1:8b"

    # Twilio (optional)
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_WHATSAPP_NUMBER: str = ""
    WEBHOOK_SECRET: str = ""

    # Storage (optional)
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""

    # Razorpay
    RAZORPAY_PAYMENT_PAGE_ID: str = "pl_SMfNZnTLY1GTcY"
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""

    # Sales
    SALES_TEAM_PHONE: str = "918800804580"
    LEAD_ALERT_THRESHOLD: int = 70

    # Limits
    GEMINI_DAILY_LIMIT: int = 1500
    GROQ_RPM_LIMIT: int = 30
    MAX_CONVERSATION_HISTORY: int = 20
    MAX_REPLY_TOKENS: int = 600

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
