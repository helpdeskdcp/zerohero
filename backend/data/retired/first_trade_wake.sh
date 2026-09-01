#!/bin/bash
# One-shot: exit the moment the first paper trade row appears (or after runner
# cutoff), so the agent is re-invoked to ping.
set -u
DB="/root/zerohero/backend/data/chanakya.db"
DEADLINE=$(date -d "$(date +%F) 15:05:00" +%s)
q() { sqlite3 -noheader "$DB" "$1" 2>/dev/null; }
while :; do
  N=$(q "SELECT count(*) FROM ai_paper_trades;")
  [ "${N:-0}" -gt 0 ] && { echo "FIRST_TRADE $(date -Iseconds)"; q "SELECT trade_id||' | '||opened_ts||' | '||strategy||' | '||coalesce(underlying,'')||coalesce(strike,'')||coalesce(option_type,'')||' '||coalesce(direction,'')||' | entry='||coalesce(entry,'')||' sl='||coalesce(stop_loss,'')||' t1='||coalesce(target_1,'')||' | status='||status FROM ai_paper_trades ORDER BY opened_ts LIMIT 1;"; exit 0; }
  [ "$(date +%s)" -ge "$DEADLINE" ] && { echo "NO_TRADE_BY_CUTOFF $(date -Iseconds)"; exit 0; }
  NOW=$(date +%H%M)
  if [ "$NOW" -ge 0915 ] && [ "$NOW" -le 1505 ]; then sleep 30; else sleep 600; fi
done
