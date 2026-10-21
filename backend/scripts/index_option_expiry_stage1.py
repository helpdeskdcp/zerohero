"""H-OPT-EXP premise test (spec: data/research/index_event/SPEC_OPTION_LEVEL_2026-10-21.md). Read-only."""
import json, os, sqlite3, sys
from datetime import date
import numpy as np
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from app.arena import settle as S
from app.instrument_profiles import get_instrument_profile
LOT = int(get_instrument_profile("NIFTY").lot_size.value)
rng = np.random.default_rng(7)
db = sqlite3.connect(f"file:{os.path.join(HERE, 'data', 'historical', 'upstox', 'upstox_research.db')}?mode=ro", uri=True, timeout=60)
expiries = [r[0] for r in db.execute("SELECT DISTINCT expiry FROM normalized_option_bars WHERE underlying='NIFTY' ORDER BY expiry")]


def day_bars(expiry, day):
    """{(strike, 'CE'|'PE'): {'HH:MM': (o, c)}} for that trading day of that expiry's contracts"""
    out = {}
    for ts, k, ot, o, c in db.execute("SELECT timestamp,strike,option_type,open,close FROM normalized_option_bars WHERE underlying='NIFTY' AND expiry=? AND substr(timestamp,1,10)=?", (expiry, day)):
        out.setdefault((k, ot), {})[ts[11:16]] = (o, c)
    return out


def straddle(expiry, day, s):
    b = day_bars(expiry, day); strikes = sorted({k for k, _ in b})
    cand = [(abs(b[(k, "CE")]["14:25"][1] - b[(k, "PE")]["14:25"][1]), k) for k in strikes if (k, "CE") in b and (k, "PE") in b and "14:25" in b[(k, "CE")] and "14:25" in b[(k, "PE")]]
    if not cand: return None, "no ATM candidates at 14:25"
    k = min(cand)[1]; need = ("14:30", "15:10")
    if not all(t in b[(k, ot)] for ot in ("CE", "PE") for t in need): return None, "missing 14:30/15:10 bar"
    gross = cost = ent = 0.0
    for ot in ("CE", "PE"):
        e = b[(k, ot)]["14:30"][0] * (1 + s); x = b[(k, ot)]["15:10"][1] * (1 - s)
        c = S.cost_points("NIFTY", e, x, LOT); 
        if c is None: return None, "no cost model"
        gross += (x - e) * LOT; cost += c * LOT; ent += e
    return {"strike": k, "net": gross - cost, "gross_after_spread": gross, "costs": cost, "entry_premium": ent}, None


def prev_day(expiry, day):
    r = db.execute("SELECT MAX(substr(timestamp,1,10)) FROM normalized_option_bars WHERE underlying='NIFTY' AND expiry=? AND substr(timestamp,1,10)<?", (expiry, day)).fetchone()
    return r[0]


def boot(a, n=5000):
    a = np.array(a); m = [rng.choice(a, len(a)).mean() for _ in range(n)]
    return float(a.mean()), float(np.percentile(m, 1.25)), float(np.percentile(m, 98.75))


res = {"spec": "SPEC_OPTION_LEVEL_2026-10-21.md", "lot": LOT, "n_expiries_in_db": len(expiries)}
for s in (0.015, 0.0, 0.010, 0.020):
    rows, skipped, ctrl = [], [], []
    for e in expiries:
        r, why = straddle(e, e, s)
        if r is None: skipped.append((e, why)); continue
        r["expiry"] = e; r["era"] = "THU" if e < "2025-09-01" else "TUE"; rows.append(r)
        pd_ = prev_day(e, e)
        if pd_:
            c, _ = straddle(e, pd_, s)
            if c: ctrl.append(c["net"])
    key = f"s={s:.3f}"
    if len(rows) < 30: res[key] = {"n_used": len(rows), "verdict": "INSUFFICIENT", "skipped": skipped}; continue
    rows.sort(key=lambda r: r["expiry"]); net = [r["net"] for r in rows]; h = len(rows) // 2
    m, lo, hi = boot(net); mA, mB = float(np.mean(net[:h])), float(np.mean(net[h:]))
    res[key] = {"n_used": len(rows), "mean_net_rs": m, "ci975": [lo, hi], "median_net_rs": float(np.median(net)), "win_rate": float(np.mean(np.array(net) > 0)), "mean_first_half": mA, "mean_second_half": mB,
                "mean_entry_premium_rs_per_unit": float(np.mean([r["entry_premium"] for r in rows])), "mean_costs_rs": float(np.mean([r["costs"] for r in rows])),
                "by_era": {e: {"n": sum(1 for r in rows if r["era"] == e), "mean_net": float(np.mean([r["net"] for r in rows if r["era"] == e]))} for e in ("THU", "TUE") if any(r["era"] == e for r in rows)},
                "control_prev_day_mean_net": float(np.mean(ctrl)) if ctrl else None, "n_control": len(ctrl), "skipped": skipped}
p = res["s=0.015"]; p1 = res["s=0.010"]
if "mean_net_rs" in p:
    chk = {"n>=30": p["n_used"] >= 30, "mean>0": p["mean_net_rs"] > 0, "ci_lo>0": p["ci975"][0] > 0, "both_halves>0": p["mean_first_half"] > 0 and p["mean_second_half"] > 0, "s1.0pct_mean>0": p1.get("mean_net_rs", -1) > 0}
    res["gate"] = {"checks": chk, "premise_holds": all(chk.values())}
else: res["gate"] = {"premise_holds": "INSUFFICIENT"}
d = os.path.join(HERE, "data", "research", "index_event"); json.dump(res, open(os.path.join(d, "OPTION_LEVEL_RESULT_2026-10-21.json"), "w"), indent=1, default=str)
print(json.dumps({k: ({kk: vv for kk, vv in v.items() if kk != "skipped"} | {"n_skipped": len(v.get("skipped", []))}) if isinstance(v, dict) and "n_used" in v else v for k, v in res.items()}, indent=1, default=str))
