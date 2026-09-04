#!/usr/bin/env bash
# Sample /api/mathematics/market-map + /signal latency and append to a log.
# Cron (root), every 5 min during market hours IST (03:45-10:00 UTC), Mon-Fri:
#   */5 3-10 * * 1-5  /root/zerohero/backend/scripts/monitor_market_map.sh
#
# Read-only GET, no order path. Flags a WARN line when any call is >3s or !=200.
set -u
cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env 2>/dev/null; set +a
U="${CHANAKYA_ADMIN_USERNAME:-admin}"
P="${CHANAKYA_ADMIN_PASSWORD:-admin@1234}"
PORT="${CHANAKYA_PORT:-7060}"
BASE="http://127.0.0.1:${PORT}/api/mathematics"
LOG="data/market_map_monitor.log"

[ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 2000000 ] && mv "$LOG" "$LOG.1"

sample() {  # $1 = path, $2 = label
  local out code t body status
  out=$(curl -s -u "$U:$P" -o /tmp/mmon.json -w "%{http_code} %{time_total}" "$BASE/$1")
  code=${out% *}; t=${out#* }
  status=$(python3 -c "
import json
try:
    d=json.load(open('/tmp/mmon.json'))
    mm=d.get('market_map')
    if mm is not None:
        print(','.join(f\"{x.get('instrument')}:{x.get('status')}\" for x in mm))
    else:
        print(d.get('status','?')+ (' spot='+str(d.get('spot')) if 'spot' in d else ''))
except Exception as e:
    print('parse-fail:'+str(e))
" 2>/dev/null)
  local ts; ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  echo "$ts  $2  http=$code  ${t}s  [$status]" >> "$LOG"
  # WARN if slow or non-200
  awk -v t="$t" -v c="$code" 'BEGIN{exit !(t+0>3.0 || c!="200")}' \
    && echo "$ts  WARN  $2  http=$code  ${t}s  [$status]" >> "$LOG"
}

sample "market-map" "market-map"
sample "signal?symbol=NIFTY" "signal:NIFTY"

# --- watchdog: after 10:25 UTC (post-close), verify today's Telegram summary
#     actually landed; alert once if it didn't.
HH=$(date -u +%H); MM=$(date -u +%M)
if [ "$HH" = "10" ] && [ "$MM" -ge 25 ]; then
  IST_DAY=$(TZ=Asia/Kolkata date +%Y-%m-%d)
  SENT="data/market_map_summary_sent.log"
  MARK="data/.mm_summary_alerted_${IST_DAY}"
  if ! { [ -f "$SENT" ] && grep -q "  ${IST_DAY}  SENT ok" "$SENT"; } && [ ! -f "$MARK" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)  WARN  daily market-map summary NOT confirmed sent for ${IST_DAY}" >> "$LOG"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
      curl -s -m 8 -o /dev/null "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=⚠ market-map daily latency summary did NOT send for ${IST_DAY} — check data/market_map_summary_sent.log"
    fi
    : > "$MARK"
  fi
fi
