#!/usr/bin/env bash
# Re-run the H1/H7 structural-state performance evaluation ONCE, automatically,
# after >= 40 fresh trading sessions have been captured since 2026-09-04 (the
# end of the data the V1 / V2 evaluations were run on -- see
# backend/ORDERFLOW_H1H7_PERFORMANCE.md). Until then it just logs progress and
# stays armed. When the threshold is reached it runs the EXISTING evaluator
# unchanged (scripts/orderflow_h1h7_performance.py --source histsrc), writes the
# report + a dedicated CSV, optionally pings Telegram with the verdict line,
# and self-disables its own crontab entry.
#
# Cron (weekly): 0 7 * * 6  -- Saturdays 07:00 IST. This server's cron runs in
# the local Asia/Kolkata TZ, not UTC.
#
# READ-ONLY / RESEARCH: does not touch the frozen H1/H7 engine, the Stage-6
# baseline, trading logic, execution, broker order logic, or production
# behaviour. live_trading stays false, paper_mode stays true.
set -u
cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env 2>/dev/null; set +a

CUTOFF="2026-09-04"          # last session date the V1/V2 evals covered
NEED=40                      # fresh sessions required before the re-run fires
LOG="data/orderflow_h1h7_perf_cron.log"
REPORT="data/orderflow_h1h7_perf_cron_report.txt"
CSV="data/orderflow_h1h7_performance_auto.csv"
exec >> "$LOG" 2>&1
echo "=== run $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

FRESH=$(./venv/bin/python - "$CUTOFF" <<'PY'
import sqlite3, sys
cutoff = sys.argv[1]
try:
    con = sqlite3.connect("file:data/market_history.db?mode=ro", uri=True)
    n = con.execute(
        "SELECT COUNT(DISTINCT session_date_ist) FROM market_candles "
        "WHERE tf='5m' AND kind IN ('FUTURE','INDEX') "
        "AND symbol IN ('CRUDEOIL','NIFTY','NATURALGAS') "
        "AND session_date_ist > ?", (cutoff,)).fetchone()[0]
    print(int(n or 0))
except Exception as e:
    print(f"ERR {e!r}")
PY
)

case "$FRESH" in
  ''|ERR*) echo "could not count fresh sessions: $FRESH -- staying armed"; exit 0 ;;
esac

echo "fresh sessions since $CUTOFF: $FRESH / $NEED"
if [ "$FRESH" -lt "$NEED" ]; then
    echo "not enough fresh sessions yet -- staying armed"
    exit 0
fi

echo "threshold reached -- re-running the existing evaluators (unchanged)"
./venv/bin/python scripts/orderflow_h1h7_performance.py --source histsrc --csv "$CSV" > "$REPORT" 2>&1
rc=$?
echo "h1h7 evaluator exit=$rc  report=$REPORT  csv=$CSV"
# also: the spike / sideways_spike backtest on the now-captured NIFTY FUTURES bars
SPK_REPORT="data/orderflow_spike_backtest_cron_report.txt"
./venv/bin/python scripts/orderflow_spike_backtest_histcap.py > "$SPK_REPORT" 2>&1
echo "spike backtest exit=$?  report=$SPK_REPORT"
VERDICT=$(grep -m1 -E "NOT VALIDATED|VALIDATED|PROMISING|PROVEN" "$REPORT" 2>/dev/null | sed 's/^ *//')
SPK_VERDICT=$(grep -m1 -E "NOT VALIDATED|all gates PASS" "$SPK_REPORT" 2>/dev/null | sed 's/^ *//')
echo "h1h7 verdict: $VERDICT"
echo "spike verdict: $SPK_VERDICT"

now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cid="${TELEGRAM_CHAT_ID:-}"
if [ -n "$cid" ] && [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
    ./venv/bin/python - "$cid" "$FRESH" "$REPORT" <<'PY'
import os, sys
cid, fresh, report = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    txt = open(report).read()
except Exception:
    txt = ""
tail = "\n".join(l for l in txt.splitlines() if l.strip())[-1200:]
msg = (f"\U0001F4C8 H1/H7 perf eval auto-re-run: {fresh} fresh sessions since 2026-09-04.\n"
       f"Research/shadow only -- no engine/baseline/trading change.\n\n{tail}")
try:
    from app.connectors import telegram
    print("telegram:", telegram._send(msg, cid))
except Exception as e:
    print("telegram send failed:", repr(e))
PY
else
    echo "no Telegram creds -- report on disk only"
fi

echo "$now  RAN  fresh=$FRESH  eval_rc=$rc" >> data/orderflow_h1h7_perf_cron_done.log
echo "self-disabling cron entry"
crontab -l 2>/dev/null | grep -v "orderflow_h1h7_perf_cron.sh" | crontab -
