"""Settle the channel signals whose stop/targets were set by the ZeroHero risk module (1R/2R/3R ladder). OFFICIAL = first target (1:1) per the arena's standard rule; the 1:2 and 1:3 exits (single target, same stop)
are DESCRIPTIVE variants chosen in advance (data/research/sl_detector/SPEC_2026-10-21.md addendum). Read-only.   python scripts/neeraj_rr_report.py"""
import json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
for l in open(os.path.join(HERE, ".env")):
    if "=" in l and not l.startswith("#"): k, v = l.strip().split("=", 1); os.environ.setdefault(k, v)
from datetime import datetime, timedelta, timezone
from app.arena import api, run as R, submit as SU
c = api.cfg(); IST = timezone(timedelta(hours=5, minutes=30)); f = lambda x: datetime.fromtimestamp(x, IST).strftime("%H:%M:%S") if x else "-"
notes = {r["id"]: r.get("note", "") for r in SU.load() if r.get("type", "HUMAN_SIGNAL") == "HUMAN_SIGNAL"}
sigs = [s for s in R.human_signals(start=c["start"], end=c["end"], exclude_ids=c["exclude"]) if "risk module" in notes.get(s["id"], "")]
if not sigs: sys.exit("no risk-module signals yet")
variants = {"1:1 (official)": 0, "1:2 (descriptive)": 1, "1:3 (descriptive)": 2}; tot = {}
for name, k in variants.items():
    vs = [{**s, "targets": [s["targets"][k]]} for s in sigs]
    out = R.run(vs, market_db=os.path.join(HERE, "data", "market_history.db")); rows = [r for v in out.values() if isinstance(v, dict) and "results" in v for r in v["results"]]
    tot[name] = rows
    print(f"\n== exit at {name}")
    for r in rows: print(f"  {r['id']} {r['underlying']} {r['strike']:g} {r['type']} {r['status']} fill {r.get('fill')}@{f(r.get('fill_t'))} exit {r.get('exit')} {r.get('exit_reason')} net {None if r.get('net_rs') is None else round(r['net_rs'], 1)}")
    ok = [r for r in rows if r["status"] == "SETTLED"]; print(f"  settled {len(ok)}/{len(rows)}  net {round(sum(r['net_rs'] for r in ok), 1)}")
print("\nn < 30 => INSUFFICIENT_SAMPLE; descriptive variants are never ranked.")
