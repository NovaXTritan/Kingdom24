#!/usr/bin/env bash
# Daily backup of chatbot state — leads, conversations, alerts, LLM usage,
# log files. Compress + retain 30 days.
#
# Hook into host crontab: `0 2 * * * /opt/k24-chatbot/deploy/backup.sh`
# (or run inside a sidecar container with cron).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
STORAGE="$ROOT/storage"
BACKUPS="$STORAGE/backups"
TODAY="$(date +%F)"
TARGET_DIR="$BACKUPS/$TODAY"
TARGET_TGZ="$BACKUPS/$TODAY.tar.gz"

mkdir -p "$TARGET_DIR"

# 1. Snapshot the JSONL files (atomic copy via cp + sync)
for f in conversations.jsonl leads.jsonl alerts.jsonl llm_usage.json; do
  if [ -f "$STORAGE/$f" ]; then
    cp -p "$STORAGE/$f" "$TARGET_DIR/$f"
  fi
done

# 2. Snapshot the day's logs (cheap — they rotate daily already)
if [ -d "$STORAGE/logs" ]; then
  cp -rp "$STORAGE/logs" "$TARGET_DIR/logs"
fi

# 3. Compress
tar -czf "$TARGET_TGZ" -C "$BACKUPS" "$TODAY"
rm -rf "$TARGET_DIR"

# 4. Counts (for the log line)
LEAD_COUNT=$(tar -xzOf "$TARGET_TGZ" "$TODAY/leads.jsonl" 2>/dev/null | wc -l || echo 0)
CONV_COUNT=$(tar -xzOf "$TARGET_TGZ" "$TODAY/conversations.jsonl" 2>/dev/null | wc -l || echo 0)
SIZE_MB=$(du -m "$TARGET_TGZ" | cut -f1)

echo "{\"ts\":\"$(date -u +%FT%TZ)\",\"event\":\"backup_complete\",\"date\":\"$TODAY\",\"conversations\":$CONV_COUNT,\"leads\":$LEAD_COUNT,\"size_mb\":$SIZE_MB}"

# 5. Prune backups older than 30 days
find "$BACKUPS" -maxdepth 1 -name "*.tar.gz" -mtime +30 -delete 2>/dev/null || true
