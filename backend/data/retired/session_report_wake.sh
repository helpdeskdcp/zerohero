#!/bin/bash
# One-shot: wait for NSE close, then emit the AUTOSCALP session report from the DB.
set -u
DB="/root/zerohero/backend/data/chanakya.db"
OUT="/root/zerohero/backend/data/autoscalp_session_$(date +%F).md"
TARGET=$(date -d "$(date +%F) 15:33:00" +%s)
while [ "$(date +%s)" -lt "$TARGET" ]; do
  REM=$(( TARGET - $(date +%s) )); [ "$REM" -gt 300 ] && sleep 240 || sleep 20
done
q(){ sqlite3 -noheader "$DB" "$1" 2>/dev/null; }
{
  echo "# AUTOSCALP session $(date +%F)  (generated $(date -Iseconds))"
  echo
  echo "## Trades"
  echo '```'
  sqlite3 -header -column "$DB" "SELECT substr(opened_ts,12,5) op, substr(closed_ts,12,5) cl, option_type||strike c, entry, exit_price ex, result, pnl, exit_reason, mfe, mae FROM ai_paper_trades WHERE strategy='AUTOSCALP' ORDER BY opened_ts;"
  echo '```'
  echo "closed=$(q "SELECT count(*) FROM ai_paper_trades WHERE strategy='AUTOSCALP' AND status!='OPEN'") | wins=$(q "SELECT sum(result='WIN') FROM ai_paper_trades WHERE strategy='AUTOSCALP'") | losses=$(q "SELECT sum(result='LOSS') FROM ai_paper_trades WHERE strategy='AUTOSCALP'") | net_pts=$(q "SELECT round(sum(pnl),2) FROM ai_paper_trades WHERE strategy='AUTOSCALP' AND status!='OPEN'") | TIME_exits=$(q "SELECT sum(exit_reason='TIME') FROM ai_paper_trades WHERE strategy='AUTOSCALP' AND status!='OPEN'")"
  echo
  echo "## Decisions"
  echo '```'
  sqlite3 -column "$DB" "SELECT decision, count(*) FROM scalp_signals GROUP BY decision;"
  echo "snapshots=$(q 'SELECT count(*) FROM live_market_snapshots')  non-NO_TRADE evals: $(q "SELECT count(*) FROM live_market_snapshots WHERE decision NOT IN ('NO_TRADE','')")"
  echo '```'
  echo
  echo "## Final runner status"
  echo '```json'
  curl -s -m8 -u admin:admin@1234 http://127.0.0.1:7060/api/autoscalp/status
  echo
  echo '```'
} > "$OUT"
echo "SESSION_REPORT_READY $(date -Iseconds) :: $OUT"
