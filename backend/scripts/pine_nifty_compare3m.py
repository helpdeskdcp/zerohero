"""Compares 5-minute-chart and 3-minute-chart (Off / Micro-breakout / Pullback then resume) results on the same window (blocks A+B). Per-ENTRY statistics, bootstrap CI.
usage: pine_nifty_compare3m.py [--out file.json]"""
import json, os, random, sys, math
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); D = os.path.join(HERE, "data", "research", "nifty_expiry_pine")
sys.path.insert(0, os.path.join(HERE, "scripts")); import pine_nifty_harness as H


def entries(path):
    d = json.load(open(path)); g = {}
    for t in d["strategy"]["closed_trades"]:
        e = g.setdefault(t["entry_time"], {"pnl": 0.0, "units": 0.0, "px": t["entry_price"], "dir": 1 if t["size"] > 0 else -1, "gross": 0.0, "exit": 0})
        e["pnl"] += t["profit"]; e["units"] += abs(t["size"]); e["gross"] += (t["exit_price"] - t["entry_price"]) * e["dir"] * abs(t["size"]); e["exit"] = max(e["exit"], t["exit_time"])
    pl = H.unpack({p["title"]: [float("nan") if v is None else float(v) for v in p["values"]] for p in d["plots"]})
    last = lambda k: pl[k][-1]
    return [g[k] for k in sorted(g)], {"bull": [last("Tracker bull resolved"), last("Tracker bull wins")], "bear": [last("Tracker bear resolved"), last("Tracker bear wins")]}


def stats(es):
    n = len(es)
    if not n: return {"n": 0}
    pn = [e["pnl"] for e in es]; w = [x for x in pn if x > 0]; l = [-x for x in pn if x <= 0]
    eq = pk = dd = 0.0
    for x in pn: eq += x; pk = max(pk, eq); dd = max(dd, pk - eq)
    random.seed(11); bs = sorted(sum(random.choices(pn, k=n)) / n for _ in range(4000))
    return {"n": n, "win_rate": round(len(w) / n, 3), "net": round(sum(pn), 1), "profit_factor": round(sum(w) / sum(l), 3) if l and sum(l) else None, "expectancy": round(sum(pn) / n, 1),
            "expectancy_ci95": [round(bs[100], 1), round(bs[3900], 1)], "avg_win": round(sum(w) / len(w), 1) if w else None, "avg_loss": round(-sum(l) / len(l), 1) if l else None, "max_dd": round(dd, 1),
            "gross_before_costs": round(sum(e["gross"] for e in es), 1)}


out = {}
for name, fmt in (("5-minute chart", "blk_5m_{}.json"), ("3-minute chart, refinement Off", "blk_3m_Off_{}.json"), ("3-minute chart, Micro-breakout", "blk_3m_Microbreakout_{}.json"),
                  ("3-minute chart, Pullback then resume", "blk_3m_Pullback_then_resume_{}.json")):
    es, tr = [], {}
    for b in (os.environ.get("BLK") or "AB"):
        p = os.path.join(D, fmt.format(b))
        if os.path.exists(p):
            e, t = entries(p); es += e
    out[name] = stats(es)
print(json.dumps(out, indent=1))
if "--out" in sys.argv: json.dump(out, open(sys.argv[sys.argv.index("--out") + 1], "w"), indent=1)
