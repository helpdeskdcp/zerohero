#!/bin/bash
# Monitor the autonomous scalper across tonight's MCX evening session + all of
# tomorrow (NSE + MCX). Per-symbol focus: NIFTY / NATURALGAS / CRUDEOIL.
# JSON lines -> durable log; per-symbol report on exit.
set -u
BASE="http://127.0.0.1:7060"; AUTH="admin:admin@1234"
DB="/root/zerohero/backend/data/chanakya.db"
LOG="/root/zerohero/backend/data/ngcrude_monitor.jsonl"
REPORT="/root/zerohero/backend/data/ngcrude_session_report.md"
END_EPOCH=$(date -d "$(date -d 'tomorrow' +%F) 23:40:00" +%s)   # tomorrow 23:40 IST

q(){ sqlite3 -noheader "$DB" "$1" 2>/dev/null; }
api(){ curl -s -m 10 -u "$AUTH" "$BASE$1" 2>/dev/null; }

echo "{\"t\":\"$(date -Iseconds)\",\"event\":\"start\",\"end\":\"$(date -d @$END_EPOCH -Iseconds)\"}" >> "$LOG"

while [ "$(date +%s)" -lt "$END_EPOCH" ]; do
  TS=$(date -Iseconds); HM=$(date +%H%M)
  ST=$(api "/api/autoscalp/status?compact=1"); [ -z "$ST" ] && ST='{"_err":"no_response"}'
  CAL=$(api /api/market/calendar)
  NTR=$(q "SELECT count(*) FROM ai_paper_trades WHERE strategy='AUTOSCALP';")
  NOPEN=$(q "SELECT count(*) FROM ai_paper_trades WHERE strategy='AUTOSCALP' AND status='OPEN';")
  # per-symbol closed pnl + counts
  PS=$(q "SELECT underlying||':'||count(*)||'/'||coalesce(sum(result='WIN'),0)||'W/'||coalesce(sum(result='LOSS'),0)||'L/'||coalesce(round(sum(pnl),2),0)||'pts' FROM ai_paper_trades WHERE strategy='AUTOSCALP' AND status!='OPEN' GROUP BY underlying;" | tr '\n' ',')
  LASTDEC=$(q "SELECT symbol||'='||decision||'('||regime||')' FROM live_market_snapshots ORDER BY id DESC LIMIT 3;" | tr '\n' ',')
  echo "{\"t\":\"$TS\",\"n_trades\":$NTR,\"n_open\":$NOPEN,\"per_symbol_closed\":\"$PS\",\"last_decisions\":\"$LASTDEC\",\"status\":$ST,\"calendar\":$CAL}" >> "$LOG"
  # dense while any market open (09:00-15:35 or MCX evening to 23:35), else sparse
  if { [ "$HM" -ge 0855 ] && [ "$HM" -le 1540 ]; } || { [ "$HM" -ge 1541 ] && [ "$HM" -le 2340 ]; }; then sleep 180; else sleep 900; fi
done

# ---------- per-symbol report ----------
{
  echo "# Autonomous Scalper — NG / Crude / NIFTY session report"
  echo "_window: $(q "SELECT min(t) FROM (SELECT json_extract(value,'\$') t FROM (SELECT 1))" 2>/dev/null; date -Iseconds) generated $(date -Iseconds)_"
  echo
  for SYM in NIFTY NATURALGAS CRUDEOIL; do
    echo "## $SYM"
    echo '```'
    sqlite3 -header -column "$DB" "SELECT substr(opened_ts,12,5) op, substr(closed_ts,12,5) cl, option_type||strike c, entry, exit_price ex, result, pnl, exit_reason, mfe, mae FROM ai_paper_trades WHERE strategy='AUTOSCALP' AND underlying='$SYM' ORDER BY opened_ts;"
    echo '```'
    echo "closed=$(q "SELECT count(*) FROM ai_paper_trades WHERE strategy='AUTOSCALP' AND underlying='$SYM' AND status!='OPEN'") | wins=$(q "SELECT coalesce(sum(result='WIN'),0) FROM ai_paper_trades WHERE strategy='AUTOSCALP' AND underlying='$SYM'") | losses=$(q "SELECT coalesce(sum(result='LOSS'),0) FROM ai_paper_trades WHERE strategy='AUTOSCALP' AND underlying='$SYM'") | net_pts=$(q "SELECT coalesce(round(sum(pnl),2),0) FROM ai_paper_trades WHERE strategy='AUTOSCALP' AND underlying='$SYM' AND status!='OPEN'") | TIME_exits=$(q "SELECT coalesce(sum(exit_reason='TIME'),0) FROM ai_paper_trades WHERE strategy='AUTOSCALP' AND underlying='$SYM' AND status!='OPEN'")"
    echo
    echo "decisions:"
    sqlite3 -column "$DB" "SELECT decision, count(*) FROM live_market_snapshots WHERE symbol='$SYM' GROUP BY decision;"
    echo "regimes seen:"
    sqlite3 -column "$DB" "SELECT regime, count(*) FROM live_market_snapshots WHERE symbol='$SYM' GROUP BY regime ORDER BY 2 DESC LIMIT 6;"
    echo
  done
  echo "## Final runner status"
  echo '```json'
  api "/api/autoscalp/status?compact=1"
  echo
  echo '```'
} > "$REPORT"
echo "{\"t\":\"$(date -Iseconds)\",\"event\":\"end\",\"report\":\"$REPORT\"}" >> "$LOG"
echo "NGCRUDE_REPORT_READY $REPORT"
