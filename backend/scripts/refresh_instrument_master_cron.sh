#!/usr/bin/env bash
# Daily refresh of the cached AngelOne scrip master (app.instruments'
# data/instrument_master.json) -- see refresh_instrument_master.py's own
# docstring for why this exists (found 2026-09-30: the cache was never
# refreshed automatically, went stale for a month, and silently broke
# AUTO-expiry resolution for the Option Chain view).
#
# Cron, once daily before market open, weekdays (server TZ = Asia/Kolkata):
#   0 7 * * 1-5
set -u
cd "$(dirname "$0")/.." || exit 1
exec 200>"data/.refresh_instrument_master.lock"
flock -n 200 || exit 0
set -a; . ./.env 2>/dev/null; set +a
export TZ=Asia/Kolkata
LOG="data/refresh_instrument_master.log"
exec >> "$LOG" 2>&1

echo "=== run $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
./venv/bin/python scripts/refresh_instrument_master.py
