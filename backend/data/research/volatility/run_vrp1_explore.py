"""H-VRP1 Stage 1, EXPLORATORY on on-disk captured data (see ADDENDUM_DATA_2026-10-23.md). Read-only."""
import json, os, sqlite3, sys
from datetime import datetime
import numpy as np
from scipy.stats import spearmanr
DB = "file:/root/zerohero/backend/data/market_history.db?mode=ro"
HERE = os.path.dirname(os.path.abspath(__file__))
c = sqlite3.connect(DB, uri=True, timeout=60)
# index closes at 09:29 and 15:14 IST (03:59Z, 09:44Z), one source per date, Angel preferred
rows = c.execute("select session_date_ist, substr(bar_start,12,5), c, source from market_candles where symbol='NIFTY' and kind='INDEX' and tf='1m' and substr(bar_start,12,5) in ('03:59','09:44')").fetchall()
idx = {}
for d, hm, px, src in rows:
    cur = idx.setdefault(d, {})
    if src == "ANGELONE_CANDLES" or cur.get("src") != "ANGELONE_CANDLES":
        if hm == "03:59": cur["s0"] = px
        else: cur["s1"] = px
        cur["src"] = src if (src == "ANGELONE_CANDLES" or "src" not in cur) else cur["src"]
days = sorted(d for d, v in idx.items() if "s0" in v and "s1" in v)
move = {d: abs(idx[d]["s1"] - idx[d]["s0"]) for d in days}
cap = [r[0] for r in c.execute("select distinct session_date_ist from quote_snapshots where symbol='NIFTY' and kind='OPTION' order by 1")]
out = []; skipped = []
for d in cap:
    if d not in move or d not in days: skipped.append((d, "no index bars")); continue
    prior = [move[x] for x in days if x < d][-20:]
    if len(prior) < 20: skipped.append((d, "<20 prior")); continue
    Hm = float(np.median(prior)); S0 = idx[d]["s0"]
    q = c.execute("select expiry,strike,option_type,bid,ask,received_ts from quote_snapshots where symbol='NIFTY' and kind='OPTION' and session_date_ist=? and received_ts>=? and received_ts<=? and bid>0 and ask>0 order by received_ts",
                  (d, d + "T03:56:00", d + "T03:59:59.999999Z")).fetchall()
    last = {}
    for e, k, t, b, a, ts in q: last[(e, k, t)] = (b + a) / 2
    exps = sorted({k[0] for k in last}, key=lambda s: datetime.strptime(s, "%d%b%Y"))
    exps = [e for e in exps if datetime.strptime(e, "%d%b%Y").date().isoformat() >= d]
    if not exps: skipped.append((d, "no option mid")); continue
    E = exps[0]; ks = sorted({k[1] for k in last if k[0] == E and (E, k[1], "CE") in last and (E, k[1], "PE") in last})
    if not ks: skipped.append((d, "no CE+PE pair")); continue
    K0 = min(ks, key=lambda k: abs(k - S0)); V0 = last[(E, K0, "CE")] + last[(E, K0, "PE")]
    dte = (datetime.strptime(E, "%d%b%Y").date() - datetime.strptime(d, "%Y-%m-%d").date()).days
    if abs(K0 - S0) > 150: skipped.append((d, f"ATM gap {K0-S0:.0f}")); continue
    out.append({"day": d, "dte": dte, "S0": S0, "K0": K0, "V0": round(V0, 2), "Hm": round(Hm, 2), "M": round(move[d], 2), "x": V0 / Hm, "rho": move[d] / V0})
print("usable days", len(out), "skipped", len(skipped), skipped[:8])
g = [o for o in out if o["dte"] >= 1]
print("DTE>=1 days", len(g), "DTE0 days", sum(o["dte"] == 0 for o in out))
res = {"usable": len(out), "dte_ge1": len(g), "skipped": skipped}
if len(g) >= 8:
    x = np.array([o["x"] for o in g]); r = np.array([o["rho"] for o in g]); sp = spearmanr(x, r)
    res["spearman_x_rho_dte_ge1"] = [round(float(sp.statistic), 3), round(float(sp.pvalue), 3)]
    t = np.argsort(np.argsort(x)) * 3 // len(x)
    res["rho_by_x_tercile(low,mid,high)"] = [round(float(r[t == i].mean()), 3) for i in range(3)]
    rng = np.random.default_rng(7); lo, hi = r[t == 0], r[t == 2]
    d = [rng.choice(lo, len(lo)).mean() - rng.choice(hi, len(hi)).mean() for _ in range(2000)]
    res["tercile_diff_low_minus_high"] = [round(float(lo.mean() - hi.mean()), 3), [round(float(np.percentile(d, 2.5)), 3), round(float(np.percentile(d, 97.5)), 3)]]
res["median_V0_over_S0_bps"] = round(float(np.median([o["V0"] / o["S0"] for o in out])) * 1e4, 1) if out else None
res["median_rho"] = round(float(np.median([o["rho"] for o in out])), 3) if out else None
res["median_rho_by_dte"] = {str(k): round(float(np.median([o["rho"] for o in out if o["dte"] == k])), 3) for k in sorted({o["dte"] for o in out})}
res["verdict"] = "INSUFFICIENT_SAMPLE (exploratory; cannot pass or sink H-VRP1)"
print(json.dumps(res, indent=1)); json.dump({"summary": res, "days": out}, open(os.path.join(HERE, "results_explore.json"), "w"), indent=1)
