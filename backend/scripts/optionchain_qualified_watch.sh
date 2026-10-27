#!/usr/bin/env bash
# Option-Chain QUALIFIED-verdict watcher -> Telegram (tagged "OPTIONCHAIN").
# Logic lives in app/optionchain/qualified_watch.py (testable, importable);
# this is a thin cron wrapper -- see that module's docstring for the full
# contract (structure-only gate, dedup, no order path).
#
# Cron, every 5 min during market hours, weekdays (server TZ = Asia/Kolkata):
#   */5 8-15 * * 1-5
set -u
cd "$(dirname "$0")/.." || exit 1
# Non-blocking lock: if a previous run is still going (e.g. the host is
# CPU-starved and a normally-sub-second scan takes minutes), skip this
# invocation instead of stacking another one on top -- found 2026-09-29,
# 38 stacked instances of this exact script piled up over 3+ hours once the
# host got CPU-starved, none making progress, each new cron firing before
# the last one exited.
exec 200>"data/.optionchain_qualified_watch.lock"
flock -n 200 || exit 0
set -a; . ./.env 2>/dev/null; set +a
export TZ=Asia/Kolkata
LOG="data/optionchain_qualified_watch.log"
exec >> "$LOG" 2>&1

DOW="$(date +%u)"
[ "$DOW" -ge 6 ] && { echo "$(date '+%F %T') skip (weekend)"; exit 0; }

echo "=== run $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
nice -n 10 timeout 240 ./venv/bin/python scripts/optionchain_qualified_scan.py
