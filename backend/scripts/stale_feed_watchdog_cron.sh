#!/usr/bin/env bash
# Read-only monitoring wrapper for scripts/stale_feed_watchdog.py.
# Cron: 45 10 * * 1-5   (10:45 UTC = 16:15 IST weekdays, after the NSE/BSE close)
#   crontab -e  ->  45 10 * * 1-5 /root/zerohero/backend/scripts/stale_feed_watchdog_cron.sh
#
# Appends a timestamped full run to a log the operator reads. Sends a Telegram
# line ONLY when the watchdog exits 2 -- i.e. an ANOMALOUS index-value freeze
# outside the benign daily pre-close pattern. The daily KNOWN_PRECLOSE freeze is
# logged but never paged. Same pattern as trending_up_haircut_monitor_cron.sh.
# Touches NOTHING: no config, no trading logic, no calibration, no other cron.
# Remove with:  crontab -l | grep -v stale_feed_watchdog_cron.sh | crontab -
set -u
cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env 2>/dev/null; set +a
LOG="data/stale_feed_watchdog.log"
PY="./venv/bin/python"; [ -x "$PY" ] || PY="python3"

{
  echo "=================================================================="
  echo "=== run $(date -u +%Y-%m-%dT%H:%M:%SZ)  ($(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M %Z')) ==="
  "$PY" scripts/stale_feed_watchdog.py
  echo
} >> "$LOG" 2>&1

# --summary + exit code decide whether to page
SUMMARY="$("$PY" scripts/stale_feed_watchdog.py --summary 2>/dev/null)"
RC=$("$PY" scripts/stale_feed_watchdog.py --summary >/dev/null 2>&1; echo $?)

if [ "$RC" = "2" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ] && [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  "$PY" - "$SUMMARY" <<'PY' >> "$LOG" 2>&1
import os, sys
from app.connectors import telegram
try:
    r = telegram._send("[stale-feed watchdog] ANOMALY\n" + sys.argv[1], os.environ["TELEGRAM_CHAT_ID"])
    print("telegram send:", r)
except Exception as e:
    print("telegram send failed:", repr(e))
PY
else
  echo "no page (rc=$RC): $SUMMARY" >> "$LOG"
fi
