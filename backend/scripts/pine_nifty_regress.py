"""Regression: current nifty_expiry_intelligence.pine vs a stored baseline run (same data window). Compares every common plot series bar by bar and every closed trade.
usage: pine_nifty_regress.py baseline_run.json [--bars N] [--tf 5]"""
import argparse, json, math, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "scripts"))
import pine_nifty_harness as H, pine_nifty_audit as A
ap = argparse.ArgumentParser(); ap.add_argument("baseline"); ap.add_argument("--bars", type=int); a = ap.parse_args()
base = json.load(open(a.baseline)); nb = len(base["times"])
rows = H.candles("5"); rows = rows[-a.bars:] if a.bars else rows
assert len(rows) == nb and rows[0]["time"] == base["times"][0], (len(rows), nb)
new = A.run(rows)
bp = H.unpack({p["title"]: [float("nan") if v is None else float(v) for v in p["values"]] for p in base["plots"]}); npl = A.plots(new)
common = sorted(set(bp) & set(npl)); diff = {}
for k in common:
    bad = [i for i in range(nb) if not A.same(bp[k][i], npl[k][i], 1e-9)]
    if bad: diff[k] = (len(bad), bad[:3], [(bp[k][i], npl[k][i]) for i in bad[:2]])
key = lambda t: (t["entry_time"], round(t["entry_price"], 6), t["size"], t["exit_time"], round(t["exit_price"], 6), round(t["profit"], 6))
bt, nt = sorted(map(key, base["strategy"]["closed_trades"])), sorted(map(key, new["strategy"]["closed_trades"]))
print(json.dumps({"bars": nb, "common_plots": len(common), "plots_only_in_new": sorted(set(npl) - set(bp))[:12], "plots_only_in_baseline": sorted(set(bp) - set(npl))[:12], "plots_with_diffs": diff,
                  "trades_baseline": len(bt), "trades_new": len(nt), "trades_identical": bt == nt, "pine_summary_new": new["strategy"]["pine_summary"], "pine_summary_base": base["strategy"]["pine_summary"]}, default=str, indent=1))
json.dump(new, open(os.path.join(HERE, "data", "research", "nifty_expiry_pine", "run_full_2y_v2.json"), "w"), default=str)
