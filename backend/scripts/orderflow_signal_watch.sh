#!/usr/bin/env bash
# Order-flow smart-money signal watcher -> Telegram (tagged "ORDERFLOW").
#
# For each watched symbol, computes TODAY's volume-spike breakout setups
# (app/orderflow) and sends a card for every BUY/SELL setup that has actually
# broken out and hasn't been sent before (dedup persisted in app_settings).
# Read-only over captured OHLCV bars -- no broker calls, no order path.
#
# Cron, every 5 min during market hours, weekdays. This server's cron runs in
# the system's local Asia/Kolkata TZ (confirmed 2026-09-05), so the field is
# IST directly:  */5 8-15 * * 1-5
#
# Symbols: $ORDERFLOW_WATCH_SYMBOLS (comma list) or the default core set.
set -u
cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env 2>/dev/null; set +a
export TZ=Asia/Kolkata
LOG="data/orderflow_signal_watch.log"
exec >> "$LOG" 2>&1

SYMS="${ORDERFLOW_WATCH_SYMBOLS:-NIFTY,BANKNIFTY,FINNIFTY,SENSEX,NATURALGAS,CRUDEOIL}"
TODAY="$(date +%Y-%m-%d)"
DOW="$(date +%u)"
[ "$DOW" -ge 6 ] && { echo "$(date '+%F %T') skip (weekend)"; exit 0; }

echo "=== run $(date -u +%Y-%m-%dT%H:%M:%SZ)  today=$TODAY  syms=$SYMS ==="

./venv/bin/python - "$SYMS" "$TODAY" <<'PY'
import sys
from app.orderflow import service, notify

syms = [s.strip().upper() for s in sys.argv[1].split(",") if s.strip()]
today = sys.argv[2]

total_sent = 0
for sym in syms:
    try:
        sm = service.smart_money(sym, today)
    except Exception as e:
        print(f"  {sym}: ERROR {type(e).__name__}: {e}")
        continue
    if sm.get("status") != "OK":
        print(f"  {sym}: {sm.get('status')} ({sm.get('reason','')})")
        continue
    res = notify.push_new_signals(sym, today, sm)
    total_sent += res.get("sent", 0)
    print(f"  {sym}: spikes={sm.get('spike_count')} considered={res['considered']} "
          f"sent={res['sent']} new_marked={res['new_marked']} tg={res['chat_id_present']}")

print(f"TOTAL new signals sent: {total_sent}")
PY
