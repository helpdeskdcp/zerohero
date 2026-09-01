#!/bin/bash
# Autonomous PAPER scalper — first-session monitor.
# Polls the running :7060 app + DB through the NSE session, logs JSON lines,
# writes a human summary on exit. Read-only. No service control.
set -u
BASE="http://127.0.0.1:7060"
AUTH="admin:admin@1234"
DB="/root/zerohero/backend/data/chanakya.db"
DAY="$(date +%F)"
LOG="/root/zerohero/backend/data/autoscalp_monitor_${DAY}.jsonl"
SUM="/root/zerohero/backend/data/autoscalp_session_${DAY}.md"
END_EPOCH=$(date -d "${DAY} 15:40:00" +%s)

q() { sqlite3 -noheader -separator '|' "$DB" "$1" 2>/dev/null; }
api() { curl -s -m 10 -u "$AUTH" "$BASE$1" 2>/dev/null; }

echo "{\"t\":\"$(date -Iseconds)\",\"event\":\"monitor_start\",\"end\":\"${DAY} 15:40\"}" >> "$LOG"

while [ "$(date +%s)" -lt "$END_EPOCH" ]; do
  NOW_HM=$(date +%H%M)
  TS=$(date -Iseconds)
  ST=$(api /api/autoscalp/status); [ -z "$ST" ] && ST='{"_err":"no_response"}'
  NSIG=$(q "SELECT count(*) FROM scalp_signals;")
  NTRD=$(q "SELECT count(*) FROM ai_paper_trades;")
  NSNAP=$(q "SELECT count(*) FROM live_market_snapshots;")
  NOPEN=$(q "SELECT count(*) FROM ai_paper_trades WHERE status='OPEN';")
  PNL=$(q "SELECT coalesce(round(sum(pnl),2),0) FROM ai_paper_trades WHERE status!='OPEN';")
  SVC=$(systemctl is-active oi-dashboard.service)
  echo "{\"t\":\"$TS\",\"svc\":\"$SVC\",\"n_signals\":$NSIG,\"n_trades\":$NTRD,\"n_open\":$NOPEN,\"n_snap\":$NSNAP,\"realised_pnl\":$PNL,\"status\":$ST}" >> "$LOG"
  # dense polling only around the session, sparse otherwise
  if [ "$NOW_HM" -ge 0905 ] && [ "$NOW_HM" -le 1535 ]; then sleep 180; else sleep 900; fi
done

# ---------- summary ----------
{
  echo "# Autonomous PAPER scalper — session ${DAY}"
  echo
  echo "_generated $(date -Iseconds)_"
  echo
  echo "## Trades (ai_paper_trades)"
  echo '```'
  q "SELECT opened_ts, closed_ts, strategy, underlying, strike, option_type, direction, entry, exit_price, status, result, pnl FROM ai_paper_trades ORDER BY opened_ts;"
  echo '```'
  echo
  echo "Closed P&L total: $(q "SELECT coalesce(round(sum(pnl),2),0) FROM ai_paper_trades WHERE status!='OPEN';") pts | wins=$(q "SELECT count(*) FROM ai_paper_trades WHERE result LIKE 'WIN%' OR result='TARGET';") losses=$(q "SELECT count(*) FROM ai_paper_trades WHERE result LIKE 'LOSS%' OR result='STOP';")"
  echo
  echo "## Decisions (scalp_signals)"
  echo '```'
  q "SELECT decision, count(*) FROM scalp_signals GROUP BY decision ORDER BY 2 DESC;"
  echo '```'
  echo "Total decision rows: $(q "SELECT count(*) FROM scalp_signals;")  |  snapshots: $(q "SELECT count(*) FROM live_market_snapshots;")"
  echo
  echo "## Sample non-NO_TRADE decisions"
  echo '```'
  q "SELECT created_ts, decision, signal_type, regime, probability, ev, rr, opt_type, opt_strike FROM scalp_signals WHERE decision NOT IN ('NO_TRADE') ORDER BY created_ts LIMIT 40;"
  echo '```'
  echo
  echo "## Final status"
  echo '```json'
  api /api/autoscalp/status
  echo
  echo '```'
  echo
  echo "## Health timeline (from jsonl)"
  echo '```'
  python3 - "$LOG" <<'PY'
import json,sys
rows=[json.loads(l) for l in open(sys.argv[1]) if l.strip() and l.startswith('{"t"') and '"event"' not in l]
if not rows:
    print("no health rows"); raise SystemExit
def g(r,*p):
    x=r
    for k in p:
        x=x.get(k,{}) if isinstance(x,dict) else {}
    return x
prev=None
for r in rows:
    st=r.get("status",{}) or {}
    key=(st.get("armed"),st.get("running"),st.get("is_leader"),st.get("last_error"),
         (st.get("feed") or {}).get("connected"), r.get("svc"),
         r.get("n_signals"),r.get("n_trades"),r.get("n_open"))
    if key!=prev:
        fb=st.get("feed") or {}
        print(f"{r['t']}  svc={r.get('svc')} armed={st.get('armed')} run={st.get('running')} "
              f"leader={st.get('is_leader')} feed={fb.get('connected')} "
              f"age={fb.get('last_msg_age_sec')} err={st.get('last_error')} "
              f"sig={r.get('n_signals')} trd={r.get('n_trades')} open={r.get('n_open')} pnl={r.get('realised_pnl')}")
        prev=key
print("--- last row ---")
r=rows[-1]; st=r.get("status",{}) or {}
sg=st.get("safeguards") or {}
print(f"{r['t']}  trades_today={sg.get('trades_today')} consec_losses={sg.get('consecutive_losses')} "
      f"halt={sg.get('halt_reason')} realised={sg.get('realised_pnl_today')}")
PY
  echo '```'
} > "$SUM"
echo "{\"t\":\"$(date -Iseconds)\",\"event\":\"monitor_end\",\"summary\":\"$SUM\"}" >> "$LOG"
