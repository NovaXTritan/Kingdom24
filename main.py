"""FastAPI application — endpoints, CORS, structured logging, middleware,
guardrails, rate limiting, request tracing.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from channels.website import ChatRequest, handle_chat_request
from channels.whatsapp import handle_status_callback, handle_twilio_webhook
from config import ROOT, settings
from core import llm_router
from core.conversation import store
from core.logger import (
    bind_request_id, current_request_id, disk_check, error_burst,
    log_event, setup as setup_logging,
)
from middleware import rate_limiter
from middleware.sanitizer import is_oversized, is_valid_uuid_or_id
from services import crm, product_catalog

setup_logging()
log_event("app_start", env=settings.ENV, port=settings.PORT)

_started_at = time.time()
_health_cache: dict = {"ts": 0.0, "payload": None}


app = FastAPI(
    title="Kingdom Foods Chatbot API",
    version="2.0.0",
    docs_url="/docs" if settings.ENV != "production" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Conversation-ID"],
    expose_headers=["X-Request-ID"],
)


# ─── Middleware: request id + size guard + structured access log ─
@app.middleware("http")
async def request_envelope(request: Request, call_next):
    rid = bind_request_id()
    t0 = time.perf_counter()

    # Body size guard
    if is_oversized(request.headers.get("content-length")):
        log_event("http_413", path=request.url.path, size=request.headers.get("content-length"))
        return JSONResponse(
            status_code=413,
            content={"error": "payload_too_large", "max_bytes": settings.MAX_REQUEST_BYTES},
            headers={"X-Request-ID": rid},
        )

    response: Response | None = None
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response
    except Exception as e:  # noqa: BLE001
        log_event("http_exception", level="ERROR", path=request.url.path, error=repr(e))
        error_burst(error_type=type(e).__name__)
        return JSONResponse(
            status_code=500,
            content={
                "ok": False, "error": "internal_error",
                "fallback": "Please try again. If the issue persists, WhatsApp +91 8800804580 (India) or +1 (555) 749-5990 (Intl).",
                "request_id": rid,
            },
            headers={"X-Request-ID": rid},
        )
    finally:
        elapsed = int((time.perf_counter() - t0) * 1000)
        status = getattr(response, "status_code", 500)
        log_event(
            "http",
            method=request.method, path=request.url.path,
            status=status, latency_ms=elapsed,
        )


# ─── Auth dependency for /api/stats ──────────────────────────────
async def require_stats_key(x_api_key: str = Header(default="")) -> None:
    expected = settings.STATS_API_KEY
    if not expected:
        # Dev: no key configured → allow (matches old behaviour)
        return
    if not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid api key")


# ─── Helpers ─────────────────────────────────────────────────────
def _client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ─── Routes ──────────────────────────────────────────────────────
@app.get("/", response_class=PlainTextResponse)
async def root() -> str:
    return "Kingdom Foods Chatbot API. POST /api/chat or /webhook/whatsapp."


@app.get("/api/health")
async def health() -> dict:
    now = time.time()
    cache = _health_cache
    if cache["payload"] and now - cache["ts"] < settings.HEALTH_CACHE_SECONDS:
        return cache["payload"]

    catalog_info = product_catalog.info()
    payload = {
        "ok": True,
        "version": "2.0.0",
        "env": settings.ENV,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "uptime_seconds": int(now - _started_at),
        "llm": llm_router.usage_snapshot(),
        "catalog": catalog_info,
        "conversations": store.stats(),
        "rate_limits": rate_limiter.snapshot(),
        "storage": {
            "disk_free_mb": disk_check(),
        },
        "twilio_enabled": settings.twilio_enabled,
        "supabase_enabled": settings.supabase_enabled,
    }
    cache["payload"] = payload
    cache["ts"] = now
    return payload


@app.get("/api/stats", dependencies=[Depends(require_stats_key)])
async def stats_endpoint(request: Request) -> JSONResponse:
    ip = _client_ip(request)
    decision = await rate_limiter.check_stats_limits(ip)
    if not decision.allowed:
        return _rate_limited(decision)
    return JSONResponse({
        "today": crm.daily_stats(),
        "conversations": store.stats(),
        "llm": llm_router.usage_snapshot(),
    })


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request) -> JSONResponse:
    ip = _client_ip(request)

    # ID validation
    if req.conversation_id and not is_valid_uuid_or_id(req.conversation_id):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_conversation_id"},
        )

    # Rate limit
    decision = await rate_limiter.check_chat_limits(ip, req.conversation_id or None)
    if not decision.allowed:
        return _rate_limited(decision)

    out = await handle_chat_request(req, channel="website")
    return JSONResponse(content=out, headers={"Cache-Control": "no-store"})


@app.get("/api/chat/history/{conversation_id}")
async def history(conversation_id: str) -> dict:
    if not is_valid_uuid_or_id(conversation_id):
        raise HTTPException(status_code=400, detail="invalid_conversation_id")
    conv = store.by_id(conversation_id)
    if not conv:
        return {"conversation_id": conversation_id, "messages": [], "stage": 0}
    return {
        "conversation_id": conv.conversation_id,
        "messages": [{"role": m.role, "content": m.content, "ts": m.timestamp} for m in conv.messages],
        "stage": conv.sales_stage,
        "lead_score": conv.lead_score,
    }


# DPDP — right to be forgotten
class DeleteRequest(BaseModel):
    phone: str


@app.post("/api/data/delete")
async def delete_data(
    req: DeleteRequest,
    x_api_key: str = Header(default=""),
) -> dict:
    """Delete every record for a given phone number. Requires the stats API
    key when STATS_API_KEY is configured."""
    if settings.STATS_API_KEY and x_api_key != settings.STATS_API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")
    summary = crm.delete_by_phone(req.phone)
    log_event("dpdp_delete", **summary)
    return {
        "ok": True,
        "message": f"Data deleted for phone ending in {summary.get('phone_masked', '****')}",
        **summary,
    }


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request) -> Response:
    return await handle_twilio_webhook(request)


@app.post("/webhook/whatsapp/status")
async def whatsapp_status(request: Request) -> dict:
    return await handle_status_callback(request)


# ─── Static widget ───────────────────────────────────────────────
WIDGET_DIR = ROOT / "widget"
if WIDGET_DIR.exists():
    app.mount("/widget", StaticFiles(directory=str(WIDGET_DIR)), name="widget")

    @app.get("/widget.js", response_class=PlainTextResponse)
    async def widget_js() -> Response:
        path = WIDGET_DIR / "widget.js"
        if not path.exists():
            return PlainTextResponse("// widget not built", status_code=404)
        return FileResponse(path, media_type="application/javascript")


# ─── Helpers ─────────────────────────────────────────────────────
def _rate_limited(decision) -> JSONResponse:
    body = {
        "error": "rate_limited",
        "reason": decision.reason,
        "retry_after_seconds": decision.retry_after,
        "message": "Bahut saare messages aa rahe hain. Thoda ruk kar try karo ya seedha call karo: 8800804580 / 15557495990",
    }
    return JSONResponse(
        status_code=429,
        content=body,
        headers={"Retry-After": str(decision.retry_after), "X-Request-ID": current_request_id()},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=False)
