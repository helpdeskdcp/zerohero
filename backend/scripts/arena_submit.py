"""Submit a HUMAN signal BEFORE taking the trade (server stamps the time; hash-chained; cannot be edited).
usage: arena_submit.py --participant NAME --underlying NIFTY --expiry 27OCT2026 --strike 23000 --type CE --entry 100 --sl 85 --targets 120,140 [--mode ABOVE|NEAR|MARKET] [--note TEXT]"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.arena import submit as SU

ap = argparse.ArgumentParser()
for k in ("participant", "underlying", "expiry", "strike", "type", "entry", "sl", "targets"): ap.add_argument(f"--{k}", required=True)
ap.add_argument("--mode", default="NEAR"); ap.add_argument("--note", default="")
a = ap.parse_args()
try:
    r = SU.submit({"participant": a.participant, "underlying": a.underlying, "expiry": a.expiry, "strike": a.strike, "option_type": a.type, "entry_mode": a.mode, "entry": a.entry, "sl": a.sl,
                   "targets": [x for x in a.targets.split(",") if x], "note": a.note})
except SU.SubmissionError as e:
    sys.exit(f"REJECTED: {e}")
print(json.dumps({k: r[k] for k in ("id", "server_iso", "underlying", "expiry", "strike", "option_type", "entry_mode", "entry", "sl", "targets", "hash")}, indent=1))
