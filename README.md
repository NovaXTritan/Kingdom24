# Kingdom Foods Chatbot

Standalone Python service powering the AI sales chatbot for **kingdom24.in**.

| | |
|---|---|
| **Stack** | Python 3.11+ · FastAPI · httpx |
| **LLMs** | Gemini Flash → Groq Llama 70B → Ollama → deterministic fallback |
| **Channels** | Website widget (REST `/api/chat`) + WhatsApp via Twilio |
| **Storage** | Supabase (optional) → local JSON in `./storage/` |
| **Cost** | ₹0 / month at the LLM layer (all free tiers) |
| **Deployment** | Docker Compose on Hetzner CX31 |

The service runs **end-to-end with zero API keys** — the LLM router cascades
through providers and falls through to a deterministic responder that does
catalog search, tier-based pricing and Razorpay link composition. Wire keys
in one at a time as you provision them.

---

## Quick start

```bash
cd k24-chatbot

# 1. Virtual env
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate         # macOS / Linux

# 2. Install
pip install -r requirements.txt

# 3. Copy env template (you can leave keys blank for now)
cp .env.example .env

# 4. Run
uvicorn main:app --reload --port 8000

# 5. Smoke test
curl -s http://localhost:8000/api/health | python -m json.tool

curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"conversation_id":"t1","message":"Quote dal makhani for 100 kg per month","metadata":{"page_url":"/","referrer":"","timestamp":"2026-05-01T00:00:00Z"}}' \
  | python -m json.tool
```

---

## How the LLM router works

```
customer_facing  →  Gemini Flash  →  Groq 70B  →  Ollama  →  deterministic
classification   →  Groq 8B       →  Gemini    →  Ollama  →  keyword shortcuts
reasoning        →  Groq 70B      →  Gemini    →  Ollama  →  deterministic
batch            →  Ollama only
```

Daily Gemini count and per-minute Groq count are tracked in-process. When a
provider hits its limit (or fails) the router transparently moves to the next
one. `GET /api/health` exposes the current usage snapshot.

---

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/chat` | Website widget chat |
| `GET`  | `/api/chat/history/{conversation_id}` | Replay a conversation |
| `POST` | `/webhook/whatsapp` | Twilio inbound message |
| `POST` | `/webhook/whatsapp/status` | Twilio delivery callbacks |
| `GET`  | `/api/health` | LLM usage + active conversation count |
| `GET`  | `/api/stats` | Daily lead funnel |
| `GET`  | `/widget/chatbot-widget.html` | Standalone widget preview |
| `GET`  | `/widget.js` | One-line embed loader |

---

## Wiring to the website

On the Next.js site, set:

```
NEXT_PUBLIC_CHATBOT_URL=https://chatbot.kingdom24.in/api/chat
```

The website widget already calls this — no UI changes needed.

For embedding the standalone widget on **any** page (e.g. a marketing landing
page outside the Next.js app):

```html
<script src="https://chatbot.kingdom24.in/widget.js" defer></script>
```

The script appends the chat trigger and panel to `<body>`, fetches its own
styles, and uses `localStorage` to persist conversation IDs.

---

## Wiring to WhatsApp

1. Create a Twilio account, enable the WhatsApp Sandbox (or get an approved number).
2. Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_NUMBER`.
3. Set `WEBHOOK_SECRET` to enable signature validation.
4. Point the WhatsApp number's webhook to:
   ```
   POST https://chatbot.kingdom24.in/webhook/whatsapp
   ```
5. Test by messaging the number.

The same `handle_chat_request` brain serves both channels — WhatsApp wraps
the response into TwiML, splitting at 1500 chars if needed.

---

## File map

| | |
|---|---|
| `config.py` | Settings + env loading (Pydantic) |
| `main.py` | FastAPI app, routes, CORS, structured logging |
| `core/llm_router.py` | Multi-LLM cascading router with rate limits |
| `core/intent_classifier.py` | Keyword shortcuts + Groq 8B fallback |
| `core/conversation.py` | In-memory + JSONL conversation store |
| `core/lead_tracker.py` | Regex + light LLM lead extraction & scoring |
| `services/product_catalog.py` | Catalog search (English / Hindi / Hinglish) |
| `services/delivery_calculator.py` | Per-zone shipping + packaging |
| `services/razorpay_links.py` | Payment-page URL composition |
| `services/crm.py` | Lead persistence + Twilio sales alert |
| `channels/website.py` | REST `/api/chat` handler |
| `channels/whatsapp.py` | Twilio webhook + signature validation |
| `channels/fallback_responder.py` | Deterministic responder when LLMs down |
| `prompts/system_prompt.py` | The 5-stage sales playbook |
| `prompts/intent_prompt.py` | Intent classification prompt |
| `prompts/templates.py` | Template responses (NON_B2B, contact, etc.) |
| `data/products.json` | 32 SKU seed catalog |
| `data/categories.json` | Category definitions + store links |
| `data/delivery_zones.json` | Per-city shipping rules |
| `widget/chatbot-widget.html` | Standalone widget preview |
| `widget/widget.js` | One-line embed loader |
| `tests/` | pytest tests for each module |
| `deploy/` | Dockerfile, docker-compose, nginx |

---

## Running tests

```bash
pip install -r requirements.txt
pytest -q
```

Tests cover:
- Catalog loads + Hindi/Hinglish search
- Lead extraction (phone, city, business type, volume in kg/tons)
- Intent classification keyword shortcuts
- Delivery cost rules (Noida free, NCR per-kg, metro 30-kg minimum)
- Razorpay link generation (param shape, phone normalisation)
- End-to-end `/api/chat` with no keys → deterministic path

---

## Deploying to Hetzner CX31

The K24 Hetzner server (4 vCPU, 8 GB RAM) runs the Compose stack defined in
`deploy/docker-compose.yml`:

- `api` — FastAPI app on 127.0.0.1:8000
- `ollama` — fallback LLM, pulls `llama3.1:8b` on first boot
- `nginx` — TLS termination, reverse proxy, security headers

```bash
ssh root@<hetzner-ip>
git clone <repo> k24-chatbot && cd k24-chatbot

# First-time SSL via certbot (run on the host, not in a container)
certbot certonly --webroot -w /var/www/certbot -d chatbot.kingdom24.in

cp .env.example .env  # fill in keys

cd deploy
docker compose up -d

# Verify
curl -fsS https://chatbot.kingdom24.in/api/health | jq
```

Logs:
```bash
docker compose logs -f api
```

---

## Security & compliance

- Twilio signature validated on every WhatsApp webhook (when `WEBHOOK_SECRET` set).
- CORS limited to `kingdom24.in` origins by default.
- HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy enforced by nginx.
- No card data ever touches the service — Razorpay handles all PCI scope.
- DPDP-compliant: lead data is collected only with active business intent and
  stored against a deterministic conversation ID (not a tracking cookie).

---

## Hard rules in the system prompt — please honour

1. B2B only. Politely redirect home-consumer queries.
2. Prices come from `data/products.json` — never invented.
3. Certifications: FSSAI + ISO 22000:2018 only.
4. All orders prepaid. No credit terms unless management approves.
5. Always match the customer's language (Hindi / English / Hinglish).
6. Concise — 2–4 short paragraphs max.
7. Always advance toward a close (trial / sample / call).

These are encoded in `prompts/system_prompt.py` — don't dilute them.

---

## Costs (for reference)

| Provider | Free tier | Used for |
|---|---|---|
| Google Gemini Flash | 1,500 requests / day | customer-facing replies |
| Groq Llama 8B / 70B | 30 RPM | classification / reasoning |
| Ollama (self-hosted) | unlimited | fallback when free tiers exhaust |

A typical month at 200 conversations / day stays comfortably inside free
tiers. Operational cost is the Hetzner CX31 (~₹1,200/month) — same server
the existing infrastructure already runs on.
