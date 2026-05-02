#!/usr/bin/env bash
# ============================================================
# Kingdom Foods chatbot — one-shot Hetzner setup script.
#
# Run this on a fresh Ubuntu 22.04+ Hetzner box AFTER:
#   1. You SSH in as root (or with sudo)
#   2. DNS chatbot.kingdom24.in → this server's IP (verify with: dig +short chatbot.kingdom24.in)
#   3. You've cloned this repo to /opt/k24-chatbot
#   4. You've populated /opt/k24-chatbot/.env with real keys
#
# Usage:
#   cd /opt/k24-chatbot
#   bash deploy/setup.sh
#
# Idempotent: re-running is safe — it skips work that's already done.
# ============================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="chatbot.kingdom24.in"
EMAIL="sales@kingdom24.in"
LE_LIVE="/etc/letsencrypt/live/${DOMAIN}"

log()    { printf '\n\033[1;32m▶ %s\033[0m\n' "$*"; }
warn()   { printf '\n\033[1;33m! %s\033[0m\n' "$*"; }
err()    { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ─── 0. Sanity ──────────────────────────────────────────────
[[ "$EUID" -eq 0 ]] || err "Run as root (or with sudo)."
[[ -f "${REPO_ROOT}/.env" ]] || err "Missing .env at ${REPO_ROOT}/.env — copy .env.example and fill keys."
[[ -f "${REPO_ROOT}/deploy/Dockerfile" ]] || err "Repo seems incomplete — no deploy/Dockerfile."

log "Server: $(hostname) · IP: $(curl -fsS ifconfig.me 2>/dev/null || echo unknown)"

# ─── 1. APT base ────────────────────────────────────────────
log "Installing system packages…"
apt-get update -qq
apt-get install -y -qq curl git nano jq dnsutils certbot ca-certificates

# ─── 2. Docker ──────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  log "Installing Docker engine…"
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
else
  log "Docker already installed: $(docker --version)"
fi

if ! docker compose version &>/dev/null 2>&1; then
  log "Installing docker-compose plugin…"
  apt-get install -y -qq docker-compose-plugin
fi

# ─── 3. DNS sanity ──────────────────────────────────────────
log "Checking DNS for ${DOMAIN}…"
RESOLVED="$(dig +short "${DOMAIN}" @1.1.1.1 | tail -n1 || true)"
HOSTIP="$(curl -fsS ifconfig.me 2>/dev/null || true)"
if [[ -n "${RESOLVED}" && "${RESOLVED}" == "${HOSTIP}" ]]; then
  log "DNS OK: ${DOMAIN} → ${RESOLVED}"
elif [[ -n "${RESOLVED}" ]]; then
  warn "DNS returns ${RESOLVED} but this host is ${HOSTIP}. Cert issuance will fail."
  warn "Fix DNS at your registrar, then re-run."
  read -rp "Continue anyway? [y/N] " yn; [[ "${yn,,}" == "y" ]] || exit 1
else
  warn "DNS for ${DOMAIN} doesn't resolve yet. Cert issuance will fail."
  warn "Set an A record pointing to ${HOSTIP}, wait 5 min, re-run."
  read -rp "Continue anyway? [y/N] " yn; [[ "${yn,,}" == "y" ]] || exit 1
fi

# ─── 4. Let's Encrypt cert ──────────────────────────────────
mkdir -p /var/www/certbot
if [[ -f "${LE_LIVE}/fullchain.pem" ]]; then
  log "TLS cert already exists at ${LE_LIVE}"
else
  log "Issuing Let's Encrypt cert (standalone mode — port 80 must be free)…"
  if ss -ltn '( sport = :80 )' | grep -q LISTEN; then
    warn "Port 80 in use — temporarily stopping nginx container if it exists."
    docker stop k24-nginx 2>/dev/null || true
  fi
  certbot certonly --standalone \
    -d "${DOMAIN}" \
    --non-interactive --agree-tos \
    -m "${EMAIL}" \
    --preferred-challenges http
fi

systemctl enable --now certbot.timer 2>/dev/null || true

# ─── 5. Storage dirs (host) ─────────────────────────────────
log "Ensuring storage directories…"
mkdir -p "${REPO_ROOT}/storage/logs" "${REPO_ROOT}/storage/backups" /opt/kingdom24-web-data
chmod 755 "${REPO_ROOT}/storage" "${REPO_ROOT}/storage/logs" "${REPO_ROOT}/storage/backups"
# Container runs as UID/GID 1000 (see deploy/Dockerfile). Bind-mounted dirs
# must be owned by 1000:1000 so the app can write conversations/leads/logs.
chown -R 1000:1000 "${REPO_ROOT}/storage"

# Provide a placeholder for the optional website-catalogue mount so docker
# doesn't fail on a non-existent file path.
if [[ ! -f /opt/kingdom24-web-data/products.json ]]; then
  echo "[]" > /opt/kingdom24-web-data/products.json
  warn "No website catalogue at /opt/kingdom24-web-data/products.json — placeholder created."
  warn "Bot will use the 32-SKU seed. To use the 526-SKU website catalogue:"
  warn "  scp Kingdom24/kingdom24-web/data/products.json root@$(hostname -I | awk '{print $1}'):/opt/kingdom24-web-data/products.json"
fi

# ─── 6. Build & start the stack ─────────────────────────────
log "Building containers…"
cd "${REPO_ROOT}/deploy"
docker compose build --no-cache --quiet api

log "Starting stack…"
docker compose up -d

log "Waiting for Ollama to come up…"
for i in $(seq 1 60); do
  if docker exec k24-ollama curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
    log "Ollama responsive (attempt ${i})"; break
  fi
  sleep 2
done

log "Ensuring llama3.1:8b model present (this may take 2-4 min on first run)…"
docker exec k24-ollama ollama pull llama3.1:8b || warn "Ollama pull failed — fallbacks remain functional."

# ─── 7. Health probe ────────────────────────────────────────
log "Probing /api/health locally…"
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null; then
    log "API responding"; break
  fi
  sleep 1
done
curl -fsS http://127.0.0.1:8000/api/health | jq '{ok, env, llm: .llm | map_values(.circuit), catalog: .catalog.product_count}' || warn "jq pretty-print failed (non-fatal)"

# ─── 8. Public probe ────────────────────────────────────────
log "Probing https://${DOMAIN}/api/health from the public internet…"
sleep 3
if curl -fsS "https://${DOMAIN}/api/health" >/dev/null; then
  log "Public endpoint live"
else
  warn "Public probe failed — check 'docker logs k24-nginx' and 'dig ${DOMAIN}'."
fi

# ─── 9. Cron — backup, watchdog, cert renew ─────────────────
log "Installing cron jobs (backup, watchdog, cert renew)…"
chmod +x "${REPO_ROOT}/deploy/backup.sh" "${REPO_ROOT}/deploy/watchdog.sh"

CRON_BACKUP="30 20 * * * ${REPO_ROOT}/deploy/backup.sh >> /var/log/k24-backup.log 2>&1"
CRON_WATCHDOG="*/5 * * * * ${REPO_ROOT}/deploy/watchdog.sh >> /var/log/k24-watchdog.log 2>&1"
CRON_RENEW='0 3 * * 1 certbot renew --quiet --post-hook "docker restart k24-nginx"'

(crontab -l 2>/dev/null | grep -Fv 'k24-' ; echo "${CRON_BACKUP}"; echo "${CRON_WATCHDOG}"; echo "${CRON_RENEW}") | crontab -

# ─── 10. logrotate ──────────────────────────────────────────
cat > /etc/logrotate.d/k24-chatbot <<'LR'
/var/log/k24-*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 644 root root
}
LR

# ─── 11. Final summary ──────────────────────────────────────
echo
echo "=========================================================================="
log "DEPLOYMENT COMPLETE"
echo "=========================================================================="
echo
echo "  Public:    https://${DOMAIN}/api/health"
echo "  Stats:     https://${DOMAIN}/api/stats   (X-API-Key required)"
echo "  Chat:      POST https://${DOMAIN}/api/chat"
echo "  WhatsApp:  POST https://${DOMAIN}/webhook/whatsapp  (point Twilio here)"
echo "  Widget:    https://${DOMAIN}/widget.js"
echo
echo "  Verify:    bash deploy/verify.sh"
echo "  Logs:      docker compose -f deploy/docker-compose.yml logs -f api"
echo "  Restart:   docker compose -f deploy/docker-compose.yml restart"
echo
echo "  Crons installed:"
crontab -l | grep -E 'k24|certbot' || true
echo
