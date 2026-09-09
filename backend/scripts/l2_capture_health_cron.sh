#!/usr/bin/env bash
# Daily L2 SnapQuote capture health check. Runs after the trading sessions
# (NSE 15:30 + MCX ~23:30 IST) -> schedule ~23:55 IST on weekdays.
# Writes data/research/orderflow/health/l2_health_<date>.{md,json} + appends the
# running TSV log. Capture QA only -- no model, no order path.
set -euo pipefail
cd "$(dirname "$0")/.."                      # -> backend/
LOG_DIR="data/research/orderflow/health"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y-%m-%d)"
{
  echo "=== l2 capture health $(date -Is) ==="
  ./venv/bin/python -m app.l2capture.health
} >> "$LOG_DIR/l2_health_cron.log" 2>&1

# optional: push the one-line verdict to Telegram if creds are present
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  MD="$LOG_DIR/l2_health_${STAMP}.md"
  [ -f "$MD" ] && SUMMARY="$(sed -n '1,6p' "$MD")" || SUMMARY="l2 health: no report for ${STAMP}"
  curl -sf -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${SUMMARY}" >/dev/null 2>&1 || true
fi
