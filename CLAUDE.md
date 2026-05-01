# Kingdom Foods Chatbot — project context

This is the standalone Python service that powers the AI sales chatbot for
**kingdom24.in**. It's deliberately separated from the Next.js website so it
can scale and evolve independently.

## At a glance

| | |
|---|---|
| Language | Python 3.11+ |
| Framework | FastAPI |
| LLMs | Google Gemini Flash (primary) → Groq Llama 70B → Ollama → deterministic fallback |
| Channels | Website widget (REST `/api/chat`) + WhatsApp via Twilio |
| Storage | Supabase (optional) → local JSON in `./storage/` |
| Cost | ₹0/month — all free tiers |
| Deployment | Hetzner CX31 via Docker Compose |

## Architecture

```
client (website widget OR Twilio WhatsApp)
   │
   ▼
FastAPI (main.py)
   │  /api/chat    or  /webhook/whatsapp
   ▼
ConversationManager  ◄── persists to ./storage/ (or Supabase)
   │
   ├─► IntentClassifier    (Groq 8B → ~100ms)
   ├─► ProductCatalog      (data/products.json)
   ├─► LeadTracker         (regex + light LLM extraction)
   │
   └─► LLMRouter           (Gemini → Groq 70B → Ollama → deterministic)
         │
         ▼
       Reply text + product cards + payment link + quick actions
```

## How to run

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt

# 2. Copy env template
cp .env.example .env       # then fill in keys you have

# 3. Run
uvicorn main:app --reload --port 8000

# 4. Smoke test
curl -s http://localhost:8000/api/health
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"conversation_id":"t1","message":"Quote 200kg paneer momos","metadata":{"page_url":"/","referrer":"","timestamp":"2026-05-01T00:00:00Z"}}'
```

The service runs end-to-end **without any API keys**. Add Gemini/Groq keys
later for genuine LLM responses; until then the deterministic fallback
handles the playbook over the catalog.

## Wiring to the website

Set on the Next.js site:

```
NEXT_PUBLIC_CHATBOT_URL=https://chatbot.kingdom24.in/api/chat
```

The website widget already calls this URL — no UI changes needed.

## Wiring to WhatsApp

1. Create a Twilio account, enable WhatsApp Sandbox or get an approved number.
2. Set `TWILIO_*` env vars.
3. Point the WhatsApp number's webhook to:
   `POST https://chatbot.kingdom24.in/webhook/whatsapp`
4. Test by messaging the number.

## File map

```
config.py                    Settings + env loading (Pydantic)
main.py                      FastAPI app, routes, CORS, logging

core/llm_router.py           Multi-LLM router with cascade + rate limits
core/intent_classifier.py    Groq-8B intent classifier with keyword fallback
core/conversation.py         Conversation state, sales-stage progression
core/lead_tracker.py         Regex + LLM lead extraction, scoring

services/product_catalog.py  Catalog search, indexed
services/delivery_calculator.py  Shipping + packaging cost
services/razorpay_links.py   Payment-link composition
services/crm.py              Lead persistence + sales alert via Twilio

channels/website.py          REST endpoint for the website widget
channels/whatsapp.py         Twilio webhook with signature validation

prompts/system_prompt.py     The 5-stage sales playbook (Hindi/English/Hinglish)
prompts/intent_prompt.py     Intent classification prompt
prompts/templates.py         Deterministic response templates

data/products.json           Seed catalog (32 SKUs across 7 categories)
data/categories.json         Category definitions + store links
data/delivery_zones.json     Per-city shipping rules

widget/chatbot-widget.html   Standalone embeddable chat widget
widget/widget.js             One-line embed loader

tests/                       pytest tests for router, intent, products, Hindi
deploy/                      Dockerfile, docker-compose.yml, nginx.conf
```

## Hard rules — please honour

1. **B2B only.** Reject individual / home-consumer requests politely.
2. **No invented prices.** All pricing comes from `data/products.json`.
3. **No invented certifications.** FSSAI + ISO only.
4. **Prepaid orders only.** No credit terms unless management explicitly allows.
5. **Match the customer's language.** Don't force English.
6. **Concise.** 2–4 short paragraphs max per reply.
7. **Always advance the sale.** Every reply moves toward trial / sample / call.

These are encoded in `prompts/system_prompt.py` — don't dilute them.
