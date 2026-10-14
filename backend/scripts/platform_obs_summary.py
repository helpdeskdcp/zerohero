"""Summarise a platform observer log (jsonl). Read-only. Usage: python scripts/platform_obs_summary.py <obs.jsonl> [HH:MM HH:MM]"""
import collections
import datetime as dt
import json
import sys

rows = [json.loads(l) for l in open(sys.argv[1])]
P = dt.datetime.fromisoformat
lo, hi = (sys.argv[2], sys.argv[3]) if len(sys.argv) >= 4 else ("00:00", "23:59")
rows = [r for r in rows if lo <= r["t"][11:16] <= hi]
print(f"rows={len(rows)} first={rows[0]['t']} last={rows[-1]['t']}")
miss = [(a["t"][11:19], b["t"][11:19], int((P(b["t"]) - P(a["t"])).total_seconds())) for a, b in zip(rows, rows[1:]) if (P(b["t"]) - P(a["t"])).total_seconds() > 75]
print("missing monitoring periods (>75 s between samples):", miss or "none")
print("sample errors:", sum(1 for r in rows if "err" in r))
seen, longest = [], 0.0
for r in rows:
    for s in r.get("stalls", []):
        if isinstance(s, list):                                  # obs.jsonl (v1) stored [duration, top-frame-tail] only
            s = {"since": None, "duration_sec": s[0], "stack": [s[1]]}
        k = (s.get("since"), s.get("duration_sec"))
        if k not in [x[0] for x in seen]:
            seen.append((k, r["t"][11:19], s))
print(f"loop stalls: {len(seen)}")
for (since, dur), t, s in seen:
    when = dt.datetime.fromtimestamp(since, dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime("%H:%M:%S") if since else "?"
    print(f"  at {when} IST (seen {t})  {dur}s  top app frames: " + " <- ".join(x.split("\n")[0].split("/")[-1][:60] for x in s["stack"][:3]))
tr, last = [], None
for r in rows:
    cur = (r.get("prov"), tuple(sorted((r.get("health") or {}).items())))
    if cur != last:
        tr.append((r["t"][11:19], cur)); last = cur
print("provider/health transitions:", tr)
gaps = [(r["t"][11:19], g) for r in rows for g in r.get("new_gap_events", [])]
print("gap_events (>=30 s per instrument):", len(gaps))
print("max write_errors", max((r.get("werr", 0) for r in rows), default=None), "| max lost", max((r.get("lost", 0) for r in rows), default=None), "| max queue", max((r.get("q", 0) for r in rows), default=None))
acc = [(a["t"][11:19], b["gate"].get("ACCEPTED", 0) - a["gate"].get("ACCEPTED", 0)) for a, b in zip(rows, rows[1:]) if "gate" in a and "gate" in b and b["ticks_in"] >= a["ticks_in"]]
print("accepted ticks: total", sum(x for _, x in acc), "| samples with 0 accepted:", sum(1 for _, x in acc if x == 0))
