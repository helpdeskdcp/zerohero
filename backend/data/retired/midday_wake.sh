#!/bin/bash
# One-shot: wake the agent at midday for a status check.
set -u
BASE="http://127.0.0.1:7060"; AUTH="admin:admin@1234"
DB="/root/zerohero/backend/data/chanakya.db"
TARGET=$(date -d "$(date +%F) 12:15:00" +%s)
while [ "$(date +%s)" -lt "$TARGET" ]; do
  REM=$(( TARGET - $(date +%s) ))
  if [ "$REM" -gt 600 ]; then sleep 300; else sleep 20; fi
done
echo "MIDDAY $(date -Iseconds)"
q() { sqlite3 -noheader "$DB" "$1" 2>/dev/null; }
echo "signals=$(q 'SELECT count(*) FROM scalp_signals;') trades=$(q 'SELECT count(*) FROM ai_paper_trades;') open=$(q "SELECT count(*) FROM ai_paper_trades WHERE status='OPEN';") realised_pnl=$(q "SELECT coalesce(round(sum(pnl),2),0) FROM ai_paper_trades WHERE status!='OPEN';") snap=$(q 'SELECT count(*) FROM live_market_snapshots;')"
echo "decisions_by_type:"; q "SELECT '  '||decision||' = '||count(*) FROM scalp_signals GROUP BY decision;"
curl -s -m 10 -u "$AUTH" "$BASE/api/autoscalp/status"
