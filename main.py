"""FastAPI application — endpoints, CORS, logging, error handling."""

from __future__ import annotations

import json
import logging
import logging.config
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from channels.website import ChatRequest, handle_chat_request
from channels.whatsapp import handle_status_callback, handle_twilio_webhook
from config import ROOT, settings
from core import llm_router
from core.conversation import store
from services import crm

# ─── Logging (JSON-ish, structured) ──────────────────────────────
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","name":"%(name)s","msg":"%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("k24.main")

app = FastAPI(
    title="Kingdom Foods Chatbot API",
    version="1.0.0",
    docs_url="/docs" if settings.ENV != "production" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logger(request: Request, call_next):
    rid = uuid.uuid4().hex[:10]
    t0 = time.perf_counter()
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        elapsed = int((time.perf_counter() - t0) * 1000)
        status = getattr(response, "status_code", 500)
        log.info(
            "http",
            extra={
                "rid": rid,
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "latency_ms": elapsed,
            },
        )


# ─── Routes ──────────────────────────────────────────────────────
@app.get("/", response_class=PlainTextResponse)
async def root() -> str:
    return "Kingdom Foods Chatbot API. POST /api/chat or /webhook/whatsapp."


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "env": settings.ENV,
        "llm": llm_router.usage_snapshot(),
        "twilio_enabled": settings.twilio_enabled,
        "supabase_enabled": settings.supabase_enabled,
        "conversations": store.stats(),
    }


@app.get("/api/stats")
async def stats() -> dict:
    return {
        "today": crm.daily_stats(),
        "conversations": store.stats(),
        "llm": llm_router.usage_snapshot(),
    }


@app.post("/api/chat")
async def chat(req: ChatRequest) -> JSONResponse:
    out = await handle_chat_request(req, channel="website")
    return JSONResponse(content=out, headers={"Cache-Control": "no-store"})


@app.get("/api/chat/history/{conversation_id}")
async def history(conversation_id: str) -> dict:
    conv = store.by_id(conversation_id)
    if not conv:
        return {"conversation_id": conversation_id, "messages": [], "stage": 0}
    return {
        "conversation_id": conv.conversation_id,
        "messages": [
            {"role": m.role, "content": m.content, "ts": m.timestamp}
            for m in conv.messages
        ],
        "stage": conv.sales_stage,
        "lead_score": conv.lead_score,
    }


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request) -> Response:
    return await handle_twilio_webhook(request)


@app.post("/webhook/whatsapp/status")
async def whatsapp_status(request: Request) -> dict:
    return await handle_status_callback(request)


# ─── Embeddable widget (served from the same origin) ─────────────
WIDGET_DIR = ROOT / "widget"
if WIDGET_DIR.exists():
    app.mount("/widget", StaticFiles(directory=str(WIDGET_DIR)), name="widget")

    @app.get("/widget.js", response_class=PlainTextResponse)
    async def widget_js() -> Response:
        path = WIDGET_DIR / "widget.js"
        if not path.exists():
            return PlainTextResponse("// widget not built", status_code=404)
        return FileResponse(path, media_type="application/javascript")


@app.exception_handler(Exception)
async def unhandled_exc(request: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled", extra={"path": request.url.path, "error": repr(exc)})
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": "internal_error",
            "fallback": "Please try again. If the issue persists, WhatsApp +91 8800804580.",
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=False)
