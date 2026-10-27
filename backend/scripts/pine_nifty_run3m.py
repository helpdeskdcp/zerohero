"""Runs the current script on a 3-minute chart built from stored 1-minute NIFTY bars (research worker: exact 3m chart + exact 5m security bars). usage: pine_nifty_run3m.py <mode> <out.json> [--from D] [--to D] [--tf 3]"""
import argparse, json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "scripts"))
import pine_nifty_harness as H
ap = argparse.ArgumentParser(); ap.add_argument("mode"); ap.add_argument("out"); ap.add_argument("--from", dest="d_from"); ap.add_argument("--to", dest="d_to"); ap.add_argument("--tf", default="3"); a = ap.parse_args()
src = open(os.path.join(HERE, "data", "research", "nifty_expiry_pine", "nifty_expiry_intelligence.pine"), encoding="utf-8").read()
inp = {"Chart timeframe handling (leave on Auto)": "Chart below 5 minutes", "Entry refinement mode": a.mode}
res = H.run_multi(src, tf=a.tf, d_from=a.d_from, d_to=a.d_to, inputs=inp)
assert res.get("ok"), res.get("diagnostics")
json.dump(res, open(a.out, "w"), default=str)
print(a.mode, len(res["times"]), "bars", res["strategy"]["pine_summary"])
