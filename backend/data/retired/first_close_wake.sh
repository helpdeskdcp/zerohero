#!/bin/bash
# One-shot: exit when the first AUTOSCALP paper trade resolves (OPEN -> closed).
set -u
DB="/root/zerohero/backend/data/chanakya.db"
DEADLINE=$(date -d "$(date +%F) 15:40:00" +%s)
q() { sqlite3 -noheader "$DB" "$1" 2>/dev/null; }
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  DONE=$(q "SELECT count(*) FROM ai_paper_trades WHERE strategy='AUTOSCALP' AND status!='OPEN';")
  if [ "${DONE:-0}" -gt 0 ]; then
    echo "FIRST_CLOSE $(date -Iseconds)"
    q "SELECT trade_id||' | '||coalesce(option_type,'')||coalesce(strike,'')||' | entry='||coalesce(entry,'')||' exit='||coalesce(exit_price,'')||' | result='||coalesce(result,'')||' pnl='||coalesce(pnl,'')||' | '||coalesce(exit_reason,'')||' | held '||coalesce(round((julianday(closed_ts)-julianday(opened_ts))*86400,0),'?')||'s' FROM ai_paper_trades WHERE strategy='AUTOSCALP' AND status!='OPEN' ORDER BY closed_ts LIMIT 1;"
    exit 0
  fi
  sleep 20
done
echo "NO_CLOSE_BY_1540 $(date -Iseconds)"
