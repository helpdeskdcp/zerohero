#!/usr/bin/env bash
# One-time post-deploy check: confirm the ev_r column (commit f92eb6e,
# 2026-09-04) is actually being populated by live signals once the market
# reopens. Cron, weekdays 16:15 IST (after NSE+MCX have both generated
# signals for the day). Self-disabling: once it finds >=1 row created after
# the deploy timestamp, it reports pass/fail via Telegram and removes its
# own crontab line so it doesn't run forever. If no new rows yet (holiday,
# or engine idle that day) it stays silent and armed for the next weekday.
set -u
cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env 2>/dev/null; set +a
DEPLOY_TS="2026-09-04T17:57:00+00:00"   # oi-dashboard.service restart carrying the ev_r fix
LOG="data/verify_ev_r_cron.log"
exec >> "$LOG" 2>&1
echo "=== run $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

./venv/bin/python - "$DEPLOY_TS" <<'PY'
import sqlite3, sys, datetime as dt, os

deploy_ts = sys.argv[1]
db_path = "data/chanakya.db"
con = sqlite3.connect(db_path)
con.row_factory = sqlite3.Row

def rows_since(table):
    return con.execute(
        f"SELECT symbol, ev, ev_r, rr, created_ts FROM {table} "
        f"WHERE created_ts > ? ORDER BY created_ts", (deploy_ts,)
    ).fetchall()

sig = rows_since("scalp_signals")
print(f"scalp_signals rows since deploy: {len(sig)}")

if not sig:
    print("no new rows yet -- staying armed for next weekday")
    sys.exit(0)

null_evr = [r for r in sig if r["ev_r"] is None]
by_sym = {}
for r in sig:
    by_sym.setdefault(r["symbol"], []).append(r["ev_r"])

lines = [f"🔎 ev_r verify -- {len(sig)} signals since deploy ({deploy_ts})"]
if null_evr:
    lines.append(f"❌ FAIL: {len(null_evr)}/{len(sig)} rows have ev_r=NULL")
    for r in null_evr[:5]:
        lines.append(f"  {r['symbol']} {r['created_ts']} ev={r['ev']} ev_r=NULL")
else:
    lines.append(f"✅ PASS: all {len(sig)} rows carry non-null ev_r")
for sym, vals in by_sym.items():
    v = [x for x in vals if x is not None]
    if v:
        lines.append(f"  {sym}: n={len(v)} ev_r min {min(v):.3f} max {max(v):.3f}")

msg = "\n".join(lines)
print(msg)

result = "PASS" if not null_evr else "FAIL"
now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
with open("data/verify_ev_r_result.log", "a") as f:
    f.write(f"{now}  {result}  n={len(sig)} null={len(null_evr)}\n")

cid = os.environ.get("TELEGRAM_CHAT_ID")
if cid and os.environ.get("TELEGRAM_BOT_TOKEN"):
    from app.connectors import telegram
    try:
        r = telegram._send(msg, cid)
        print("telegram send:", r)
    except Exception as e:
        print("telegram send failed:", repr(e))
else:
    print("no Telegram creds -- summary above only")

# Signal the shell wrapper to self-disable (we got a real answer either way)
sys.exit(42)
PY
rc=$?
if [ "$rc" = "42" ]; then
    echo "self-disabling cron entry"
    crontab -l 2>/dev/null | grep -v "verify_ev_r.sh" | crontab -
fi
