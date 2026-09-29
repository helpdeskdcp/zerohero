#!/usr/bin/env bash
# Hedging Engine autonomous scan -> Telegram on open/close. Logic lives in
# app/hedging/runner.py (testable, importable); this is a thin cron wrapper.
# DISARMED by default -- no entries open until POST /api/hedging/arm; exits
# on any already-open position are still monitored regardless.
#
# Cron, every 5 min during market hours, weekdays (server TZ = Asia/Kolkata):
#   */5 8-15 * * 1-5
set -u
cd "$(dirname "$0")/.." || exit 1
# Non-blocking lock -- see optionchain_qualified_watch.sh's comment for why
# (same 2026-09-29 incident: this script's instances stacked up too, 24 of
# them, once the host got CPU-starved).
exec 200>"data/.hedging_scan.lock"
flock -n 200 || exit 0
set -a; . ./.env 2>/dev/null; set +a
export TZ=Asia/Kolkata
LOG="data/hedging_scan.log"
exec >> "$LOG" 2>&1

DOW="$(date +%u)"
[ "$DOW" -ge 6 ] && { echo "$(date '+%F %T') skip (weekend)"; exit 0; }

echo "=== run $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
./venv/bin/python scripts/hedging_scan.py
