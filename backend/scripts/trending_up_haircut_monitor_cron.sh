#!/usr/bin/env bash
# Read-only monitoring wrapper for scripts/trending_up_haircut_monitor.py.
# Cron: 30 16 * * 1-5  (16:30 IST weekdays, after the NSE close) -- appends a
# timestamped full run to a log the operator reads, and sends a one-line
# Telegram summary (same pattern as verify_ev_r.sh / market_map_daily_summary.sh).
# Touches NOTHING: no config, no trading logic, no calibration, no other cron.
# Remove any time with:
#   crontab -l | grep -v trending_up_haircut_monitor_cron.sh | crontab -
set -u
cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env 2>/dev/null; set +a
LOG="data/trending_up_haircut_monitor.log"

{
  echo "=================================================================="
  echo "=== run $(date -u +%Y-%m-%dT%H:%M:%SZ)  ($(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M %Z')) ==="
  ./venv/bin/python scripts/trending_up_haircut_monitor.py
  echo
} >> "$LOG" 2>&1

# one-line Telegram summary
SUMMARY="$(./venv/bin/python scripts/trending_up_haircut_monitor.py --summary 2>/dev/null)"
if [ -n "${TELEGRAM_CHAT_ID:-}" ] && [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "$SUMMARY" ]; then
  ./venv/bin/python - "$SUMMARY" <<'PY' >> "$LOG" 2>&1
import os, sys
from app.connectors import telegram
try:
    r = telegram._send(sys.argv[1], os.environ["TELEGRAM_CHAT_ID"])
    print("telegram send:", r)
except Exception as e:
    print("telegram send failed:", repr(e))
PY
fi
