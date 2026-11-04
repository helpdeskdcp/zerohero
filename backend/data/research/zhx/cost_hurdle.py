"""COST-HURDLE gate (pre-registered 2026-11-04 before any result). The option round trip costs a FIXED amount of rupees (brokerage, STT, GST, exchange, half-spread, +2 extra orders); a trade whose stop distance is small in index points
cannot pay it. friction_pts = friction_rs / (delta x lot) per index (same cost model as every earlier study). Variants: C1 risk >= 1.0 x friction_pts | C2 >= 2.0 x | C3 >= 3.0 x (T1 is always >= 1R away, so risk >= k x friction_pts also puts T1 at least k x friction_pts away).
Selection rule and gate unchanged from the ablation study: KEEP only if pooled DEV+VALIDATION net per trade improves by >= Rs30 with >= 150 trades; ACCEPT only if on >= 2 of 3 indices OOS 90% day-cluster lower bound > 0, VALIDATION > 0 and OOS trades >= 100. 3 variants x 3 indices = 9 looks."""
import json, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))
from app.research_engines.hybrid_sr_mtf import metrics as M
src = json.load(open(os.path.join(HERE, "validate_v1d.json")))["results"]
SPL = {"NIFTY": {"DEV": (20170601, 20211231), "VALIDATION": (20220101, 20231231), "OOS": (20240101, 20261031)}, "BANKNIFTY": {"DEV": (20210929, 20221231), "VALIDATION": (20230101, 20231231), "OOS": (20240101, 20261031)},
       "SENSEX": {"DEV": (20210929, 20221231), "VALIDATION": (20230101, 20231231), "OOS": (20240101, 20261031)}}
FR = {s: M.net_rs(s, 0.0)[1] / (M.DELTA * M.LOT[s]) for s in SPL}; print("friction in index points (one round trip):", {s: round(v, 2) for s, v in FR.items()}, "rupees:", {s: round(M.net_rs(s, 0.0)[1], 1) for s in SPL})
def summ(sel, sym):
    if not sel: return {"n": 0}
    rr = [{"day": x["day"], "side": x["side"], "R": x["R"], "pts": x["pts"], "risk": x["risk"], "score": x["score"], "t_entry": x["t_entry"], "reason": "", "ambiguous": False, "mae": 0.0, "mfe": 0.0, "hit_t1": False, "hit_t2": False, "hit_t3": False} for x in sel]
    s = M.summarize(rr, sym); return {"n": s.get("n"), "gross_R": round(float(np.mean([x["R"] for x in sel])), 3), "net": s.get("net_per_trade_rs"), "ci90": s.get("ci90_per_trade"), "pf": s.get("profit_factor")}
def pick(sym, sp, k): a, z = SPL[sym][sp]; return [x for x in src[sym]["trade_rows"] if a <= x["day"] <= z and x["risk"] >= k * FR[sym]]
def pooled(k):
    tot, n = 0.0, 0
    for s in SPL:
        for sp in ("DEV", "VALIDATION"):
            r = summ(pick(s, sp, k), s)
            if r["n"]: tot += (r["net"] or 0) * r["n"]; n += r["n"]
    return (tot / n if n else None), n
base = pooled(0.0); print("baseline pooled DEV+VAL net/trade, n:", base); res = {"friction_pts": FR, "baseline": base, "variants": {}}
for name, k in (("C1", 1.0), ("C2", 2.0), ("C3", 3.0)):
    p = pooled(k); keep = p[0] is not None and p[1] >= 150 and p[0] - base[0] >= 30; res["variants"][name] = {"k": k, "dev_val": p, "kept": keep, "oos": {}}
    print(name, f"risk >= {k} x friction", "pooled DEV+VAL", (round(p[0], 1) if p[0] is not None else None), p[1], "KEPT" if keep else "-")
    passes = 0
    for s in SPL:
        o, v = summ(pick(s, "OOS", k), s), summ(pick(s, "VALIDATION", k), s); ci = o.get("ci90") or [None, None]
        ok = ci[0] is not None and ci[0] > 0 and (v.get("net") or -1) > 0 and o["n"] >= 100; passes += ok; res["variants"][name]["oos"][s] = {"OOS": o, "VAL": v, "pass": bool(ok)}
        print("   ", s, "OOS n=%s grossR=%s net=%s ci=%s | VAL n=%s net=%s | %s" % (o.get("n"), o.get("gross_R"), o.get("net"), ci, v.get("n"), v.get("net"), "PASS" if ok else "fail"))
    res["variants"][name]["gate_passes"] = passes; print("   ->", "ACCEPT" if passes >= 2 else "REJECT", f"({passes}/3)")
json.dump(res, open(os.path.join(HERE, "cost_hurdle.json"), "w"), indent=1, default=str)
