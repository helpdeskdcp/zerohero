"""Pre-registered threshold choice: min score in {5,6,7,8,9}; chosen ONLY on pooled DEV trades of the three indices (highest net rupees per trade with >= 150 DEV trades); then VALIDATION and OOS are looked at once with that single threshold.
Rule fixed before the run: if the chosen threshold's OOS lower bound of net per trade is not > 0 on at least 2 of 3 indices, ZHX stays a SHADOW-only candidate with NO claim."""
import json, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))
from app.research_engines.hybrid_sr_mtf import metrics as M
V = json.load(open(os.path.join(HERE, "validate_v1b.json")))["results"]
SPL = {"NIFTY": {"DEV": (20170601, 20211231), "VALIDATION": (20220101, 20231231), "OOS": (20240101, 20261031)}, "BANKNIFTY": {"DEV": (20210929, 20221231), "VALIDATION": (20230101, 20231231), "OOS": (20240101, 20261031)}, "SENSEX": {"DEV": (20210929, 20221231), "VALIDATION": (20230101, 20231231), "OOS": (20240101, 20261031)}}
def sel(sym, sp, thr): a, z = SPL[sym][sp]; return [t for t in V[sym]["trade_rows"] if a <= t["day"] <= z and t["score"] is not None and t["score"] >= thr]
def stats(sym, rows):
    if not rows: return {"n": 0}
    tr = [{**t, "t_entry": 0, "reason": "", "ambiguous": False, "mae": 0.0, "mfe": 0.0, "hit_t1": False, "hit_t2": False, "hit_t3": False} for t in rows]; s = M.summarize(tr, sym)
    return {"n": s["n"], "gross_R": round(float(np.mean([t["R"] for t in rows])), 3), "win": s["win_rate"], "net_per_trade": s["net_per_trade_rs"], "ci90": s["ci90_per_trade"], "pf": s["profit_factor"], "per_day": round(len(rows) / max(1, len({t["day"] for t in rows})), 2)}
out = {"dev_pooled": {}, "chosen": None, "after": {}}
for thr in (5, 6, 7, 8, 9):
    ns, tot = 0, 0.0
    for sym in V:
        r = sel(sym, "DEV", thr); st = stats(sym, r)
        if st["n"]: ns += st["n"]; tot += st["net_per_trade"] * st["n"]
    out["dev_pooled"][thr] = {"n": ns, "net_per_trade": round(tot / ns, 1) if ns else None}
ok = {k: v for k, v in out["dev_pooled"].items() if v["n"] >= 150 and v["net_per_trade"] is not None}; thr = max(ok, key=lambda k: ok[k]["net_per_trade"]) if ok else 5; out["chosen"] = thr
for sym in V: out["after"][sym] = {sp: stats(sym, sel(sym, sp, thr)) for sp in ("DEV", "VALIDATION", "OOS")}
passes = sum(1 for sym in V if (out["after"][sym]["OOS"].get("ci90") or [None])[0] is not None and out["after"][sym]["OOS"]["ci90"][0] > 0)
out["oos_indices_with_lower_bound_gt_0"] = passes; out["verdict"] = "CANDIDATE (still needs forward evidence)" if passes >= 2 else "NO DEMONSTRATED EDGE: SHADOW-only"
json.dump(out, open(os.path.join(HERE, "score_select.json"), "w"), indent=1, default=float); print(json.dumps(out, indent=1, default=float)[:3500])
