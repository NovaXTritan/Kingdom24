#!/usr/bin/env bash
# Watchdog — every 5 min check the public health endpoint. If it fails twice
# in a row, restart the api container. Logs to stdout (captured by cron).

set -uo pipefail

DOMAIN="${DOMAIN:-chatbot.kingdom24.in}"
URL="https://${DOMAIN}/api/health"
STATE_FILE="/var/run/k24-watchdog.state"

now() { date -Iseconds; }

CODE=$(curl -s -o /dev/null --max-time 10 -w "%{http_code}" "${URL}" 2>/dev/null || echo "000")

if [[ "${CODE}" == "200" ]]; then
  rm -f "${STATE_FILE}"
  echo "$(now) | OK ${URL}"
  exit 0
fi

# Track consecutive failures
FAILS=$(cat "${STATE_FILE}" 2>/dev/null || echo 0)
FAILS=$((FAILS + 1))
echo "${FAILS}" > "${STATE_FILE}"
echo "$(now) | FAIL ${URL} → HTTP ${CODE} (consecutive fails: ${FAILS})"

if [[ "${FAILS}" -ge 2 ]]; then
  echo "$(now) | ALERT 2nd consecutive failure — restarting api container"
  REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  cd "${REPO_ROOT}/deploy"
  docker compose restart api 2>&1 | sed "s/^/$(now) | /"
  rm -f "${STATE_FILE}"
fi
