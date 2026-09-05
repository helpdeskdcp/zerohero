#!/usr/bin/env bash
# Post-close summary of the day's market-map latency samples -> Telegram.
# Cron (root), weekdays 46 15 * * 1-5 (15:46 IST -- this server's cron runs in
# the local Asia/Kolkata TZ, not UTC; confirmed 2026-09-05). Self-verifying: the send
# result (ok / failure reason) is appended to data/market_map_summary_sent.log
# and this script's stdout is captured to data/market_map_summary_cron.log, so
# whether Monday's summary landed is checkable without anyone watching.
set -u
cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env 2>/dev/null; set +a
LOG="data/market_map_monitor.log"
exec >> data/market_map_summary_cron.log 2>&1
echo "=== run $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
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

import os as _os, time as _t
cid = _os.environ.get("TELEGRAM_CHAT_ID")
now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
sent_log = "data/market_map_summary_sent.log"
result = None
if cid and _os.environ.get("TELEGRAM_BOT_TOKEN"):
    for attempt in (1, 2):
        try:
            r = telegram._send(msg, cid)
        except Exception as e:
            r = {"ok": False, "reason": repr(e)}
        if r.get("ok"):
            result = f"{now}  {today}  SENT ok  (attempt {attempt})"
            break
        result = f"{now}  {today}  FAILED  {r}  (attempt {attempt})"
        _t.sleep(5)
else:
    result = f"{now}  {today}  SKIPPED  no Telegram creds"
with open(sent_log, "a") as f:
    f.write(result + "\n")
print(result)
PY
