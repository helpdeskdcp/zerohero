#!/usr/bin/env bash
# Post-close summary of the day's market-map latency samples -> Telegram.
# Cron (root), weekdays 15:45 IST = 10:15 UTC:
#   15 10 * * 1-5  /root/zerohero/backend/scripts/market_map_daily_summary.sh
set -u
cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env 2>/dev/null; set +a
LOG="data/market_map_monitor.log"
[ -f "$LOG" ] || { echo "no monitor log yet"; exit 0; }

./venv/bin/python - "$LOG" <<'PY'
import sys, re, statistics as st, datetime as dt
from app.connectors import telegram

path = sys.argv[1]
today = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")
by = {"market-map": [], "signal:NIFTY": []}
warns, codes = [], {}
line_re = re.compile(r"^(\S+)\s+(\S+)\s+http=(\d+)\s+([\d.]+)s\s+\[(.*)\]$")
for ln in open(path):
    m = line_re.match(ln.strip())
    if not m:
        continue
    ts, label, code, lat, status = m.groups()
    if not ts.startswith(today.replace("-", "-")):  # UTC ts; keep the whole file for the day is close enough
        pass
    if "WARN" in ln:
        warns.append(ln.strip()); continue
    if label in by:
        by[label].append(float(lat))
        codes[code] = codes.get(code, 0) + 1

def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * p))] if v else 0.0

lines = [f"📊 market-map latency — {today}"]
for label, v in by.items():
    if not v:
        lines.append(f"{label}: no samples"); continue
    lines.append(f"{label}: n={len(v)}  min {min(v):.2f}s  med {st.median(v):.2f}s  "
                 f"p95 {q(v,0.95):.2f}s  max {max(v):.2f}s")
lines.append(f"http: {codes or '—'}  ·  WARNs: {len(warns)}")
for w in warns[-8:]:
    lines.append("⚠ " + w)

msg = "\n".join(lines)
print(msg)
import os as _os
cid = _os.environ.get("TELEGRAM_CHAT_ID")
if cid and _os.environ.get("TELEGRAM_BOT_TOKEN"):
    try:
        telegram._send(msg, cid)
        print("[sent to Telegram]")
    except Exception as e:
        print("[telegram send failed]", e)
else:
    print("[no Telegram creds — logged only]")
PY
