#!/bin/bash
# ONE-SHOT night job (owner-approved 2026-11-05): rebuild market_history.db with auto_vacuum=INCREMENTAL (so the nightly retention can hand space back) using the verified pipeline:
# wait for the nightly retention -> compact_into.py (VACUUM INTO, service keeps running, original untouched, FK compared with the original) -> only if VERIFIED and auto_vacuum=2 -> swap_market_history.py (stops oi-dashboard ~6 min, keeps the old file as market_history.db.old, rollback on failure).
# Removes its own cron line first. Never deletes anything. Log: data/ops/auto_vacuum_migration.log
cd /root/zerohero/backend || exit 0
LOG=data/ops/auto_vacuum_migration.log; exec >> "$LOG" 2>&1
echo "=== $(date -Is) auto_vacuum migration start"
crontab -l 2>/dev/null | grep -v "scripts/auto_vacuum_migration.sh" | crontab -
for i in $(seq 1 90); do pgrep -f "[d]ata_retention.py|[p]latform_candles_retention|[l]ive_snapshots_retention|[m]ohit_filter_forward|[m]cx_uturn_forward" >/dev/null || break; echo "waiting for the nightly jobs ($i)"; sleep 60; done
if [ -e data/market_history.compact.db ] || [ -e data/market_history.db.old ]; then echo "compact.db or .old already exists: refusing (never overwrite)"; exit 0; fi
COMPACT_AUTO_VACUUM=incremental venv/bin/python scripts/compact_into.py; rc=$?; echo "compact_into rc=$rc"
v=$(venv/bin/python -c "import json;d=json.load(open('data/ops/compact_into_result.json'));print(d.get('verdict','?'), d.get('auto_vacuum_new'))")
echo "result: $v"
case "$v" in "VERIFIED (not swapped) 2") ;; *) echo "NOT verified or not auto_vacuum=2: swap skipped, original untouched, compact file left for inspection"; exit 0;; esac
venv/bin/python scripts/swap_market_history.py; echo "swap rc=$?"
echo "after: auto_vacuum=$(sqlite3 -readonly data/market_history.db 'pragma auto_vacuum') size=$(stat -c %s data/market_history.db) free=$(df -h / | tail -1 | awk '{print $4}')"
echo "=== done $(date -Is)"
