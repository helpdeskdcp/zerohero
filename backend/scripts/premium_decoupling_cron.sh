#!/usr/bin/env bash
# Premium-vs-Spot Decoupling scan -> shadow log (data/premium_decoupling.db).
# Read-only over already-captured quote_snapshots -- no broker calls, no
# order path, no gating. See app/premium_decoupling/ + scripts/premium_decoupling_scan.py.
#
# Cron, every 5 min during market hours, weekdays. This server's cron runs in
# the system's local Asia/Kolkata TZ, so the field is IST directly:
#   */5 8-15 * * 1-5
set -u
cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env 2>/dev/null; set +a
export TZ=Asia/Kolkata
LOG="data/premium_decoupling_scan.log"
exec >> "$LOG" 2>&1

DOW="$(date +%u)"
[ "$DOW" -ge 6 ] && { echo "$(date '+%F %T') skip (weekend)"; exit 0; }

echo "=== run $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
./venv/bin/python scripts/premium_decoupling_scan.py --window-sec 300
