#!/usr/bin/env bash
# Watches the subgroups flagged in CALIBRATION_OVERCONFIDENCE_AUDIT.md (K8,
# PRODUCTION_READINESS.md) for when they cross this report's own n>=20
# reliability floor. Read-only: only counts rows and sends a Telegram alert +
# log line when a subgroup FIRST crosses 20 -- it does not re-run the full
# attribution, change any config, or touch calibration/trading logic.
#
# Cron, weekdays 16:20 IST (after both NSE+MCX have generated the day's
# signals). Self-tracking per-subgroup via data/calibration_subgroups_state.json
# so each subgroup alerts exactly once, the first day it clears 20.
set -u
cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env 2>/dev/null; set +a
LOG="data/calibration_subgroups_cron.log"
STATE="data/calibration_subgroups_state.json"
exec >> "$LOG" 2>&1
echo "=== run $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

./venv/bin/python - "$STATE" <<'PY'
import json, os, sqlite3, sys, datetime as dt

state_path = sys.argv[1]
try:
    state = json.load(open(state_path))
except Exception:
    state = {}

SUBGROUPS = [
    {"key": "trending_up_regime", "label": "regime=TRENDING_UP (all symbols)",
     "where": "regime='TRENDING_UP'"},
    {"key": "ng_trenddown_supbreak", "label": "NATURALGAS / TRENDING_DOWN / SUPPORT_BREAKDOWN",
     "where": "symbol='NATURALGAS' AND regime='TRENDING_DOWN' AND signal_type='SUPPORT_BREAKDOWN'"},
    {"key": "ng_trendup_supreversal", "label": "NATURALGAS / TRENDING_UP / SUPPORT_REVERSAL",
     "where": "symbol='NATURALGAS' AND regime='TRENDING_UP' AND signal_type='SUPPORT_REVERSAL'"},
]

con = sqlite3.connect("data/chanakya.db")
con.row_factory = sqlite3.Row

newly_crossed = []
report_lines = []
for sg in SUBGROUPS:
    row = con.execute(f"""
        SELECT COUNT(*) n,
               AVG(probability) avg_pred,
               AVG(CASE WHEN outcome='WIN' THEN 1.0 ELSE 0.0 END) win_rate
        FROM scalp_signals
        WHERE source='LIVE' AND status='CLOSED' AND probability IS NOT NULL
          AND outcome IN ('WIN','LOSS','FLAT') AND {sg['where']}
    """).fetchone()
    n = row["n"] or 0
    already = state.get(sg["key"], {}).get("alerted", False)
    line = f"  {sg['label']}: n={n}"
    if n >= 20 and row["avg_pred"] is not None:
        gap = (row["avg_pred"] - row["win_rate"]) * 100
        line += f"  avg_pred={row['avg_pred']*100:.1f}%  win={row['win_rate']*100:.1f}%  gap={gap:+.1f}pp"
    report_lines.append(line)
    state.setdefault(sg["key"], {})["n"] = n
    if n >= 20 and not already:
        newly_crossed.append((sg, row))
        state[sg["key"]]["alerted"] = True
        state[sg["key"]]["crossed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()

print("current subgroup counts:")
print("\n".join(report_lines))

json.dump(state, open(state_path, "w"), indent=2)

if not newly_crossed:
    print("no subgroup newly crossed n>=20 -- staying armed")
    sys.exit(0)

msg_lines = ["\U0001F4CA Calibration subgroup(s) reached n>=20 (K8 re-check due)"]
for sg, row in newly_crossed:
    gap = (row["avg_pred"] - row["win_rate"]) * 100
    msg_lines.append(f"{sg['label']}: n={row['n']}  avg_pred={row['avg_pred']*100:.1f}%  "
                     f"win={row['win_rate']*100:.1f}%  gap={gap:+.1f}pp")
msg_lines.append("Read-only check only -- no re-analysis run, no config changed. "
                 "Ask Claude to re-run the full attribution for these subgroups.")
msg = "\n".join(msg_lines)
print(msg)

now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
cid = os.environ.get("TELEGRAM_CHAT_ID")
result = None
if cid and os.environ.get("TELEGRAM_BOT_TOKEN"):
    from app.connectors import telegram
    for attempt in (1, 2):
        try:
            r = telegram._send(msg, cid)
        except Exception as e:
            r = {"ok": False, "reason": repr(e)}
        if r.get("ok"):
            result = f"{now}  SENT ok (attempt {attempt})"
            break
        result = f"{now}  FAILED  {r}  (attempt {attempt})"
else:
    result = f"{now}  SKIPPED  no Telegram creds"
with open("data/calibration_subgroups_sent.log", "a") as f:
    f.write(result + "\n" + msg + "\n---\n")
print(result)

all_alerted = all(state.get(sg["key"], {}).get("alerted") for sg in SUBGROUPS)
if all_alerted:
    print("all 3 subgroups have alerted -- signalling self-disable")
    sys.exit(42)
PY
rc=$?
if [ "$rc" = "42" ]; then
    echo "self-disabling cron entry (all 3 subgroups reported)"
    crontab -l 2>/dev/null | grep -v "check_calibration_subgroups.sh" | crontab -
fi
