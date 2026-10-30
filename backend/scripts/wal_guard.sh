#!/bin/bash
# Keeps SQLite WAL files from eating the disk (2026-10-30: market_history.db-wal reached 8 GB while a long reader / the retention delete blocked the automatic checkpoint, and the disk hit 0 bytes free).
# PASSIVE checkpoint always (never blocks anyone); if a WAL is above LIMIT_MB, try TRUNCATE with a short busy timeout (it waits at most 5 s, writers just retry).
cd "$(dirname "$0")/../data" || exit 0
LIMIT_MB=${WAL_GUARD_LIMIT_MB:-512}
for db in market_history.db l2_capture.db chanakya.db platform_ticks.db dhan_orderflow_ticks.db; do
  [ -f "$db-wal" ] || continue
  sqlite3 "$db" "PRAGMA busy_timeout=2000; PRAGMA wal_checkpoint(PASSIVE);" >/dev/null 2>&1
  sz=$(stat -c %s "$db-wal" 2>/dev/null || echo 0)
  if [ "$sz" -gt $((LIMIT_MB*1024*1024)) ]; then
    r=$(sqlite3 "$db" "PRAGMA busy_timeout=5000; PRAGMA wal_checkpoint(TRUNCATE);" 2>&1 | tr '\n' ' ')
    echo "$(date -Is) $db wal=$((sz/1048576))MB truncate=$r"
  fi
done
