"""H_PIN Stage A/B test. Rules are FIXED in EXPERIMENT_SPEC_EXPIRY_PIN_2026-10-18.md (commit b11dbfd); do not tune here.
usage: expiry_pin_test.py A|B"""
import sys, os, json, sqlite3, datetime as dt, random, math
import numpy as np
from scipy import stats
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.research_engines.smart_bos.data import load_1m
from app.research_engines.hybrid_mp.engine import resample

STAGE = sys.argv[1] if len(sys.argv) > 1 else "A"
COST, STOP_BPS, DOM, LO, HI = 6.3, 35.0, 1.25, 15.0, 150.0
SPLIT = "2025-08-28"
IST = dt.timedelta(hours=5, minutes=30)
db = sqlite3.connect("file:data/historical/upstox/upstox_research.db?mode=ro", uri=True)
exps = [r[0] for r in db.execute("select distinct expiry from normalized_option_bars where underlying='NIFTY' and interval='5minute' order by 1")]
exps = [e for e in exps if (e >= SPLIT) == (STAGE == "A")]
b1 = load_1m("NIFTY", "INDEX", start="2024-09-01", end="2027-01-01", prefer="ANGELONE_CANDLES")
ch = resample(b1, 5, 555, 930)
bars = {}
for i, t in enumerate(ch["t"]):
    d = dt.datetime.utcfromtimestamp(int(t)) + IST
    bars[d.strftime("%Y-%m-%d %H:%M")] = i

def oi_at(e, hhmm):
    rows = db.execute("select strike, sum(open_interest) from normalized_option_bars where underlying='NIFTY' and expiry=? and interval='5minute' "
                      "and substr(timestamp,1,16)=? and open_interest is not null group by strike", (e, f"{e}T{hhmm}")).fetchall()
    return {float(k): float(v) for k, v in rows if v is not None}

def trade(e, K):
    """returns (D, side, net_bps_or_None, gross_ret_bps) for strike K"""
    i0, i1 = bars.get(f"{e} 14:30"), bars.get(f"{e} 15:10")
    if i0 is None or i1 is None: return None
    S = float(ch["o"][i0]); D = (K - S) / S * 1e4
    side = 1 if D > 0 else -1
    raw = (float(ch["c"][i1]) - S) / S * 1e4
    if not (LO <= abs(D) <= HI): return D, side, None, raw
    stop = S * (1 - side * STOP_BPS / 1e4)
    px = None
    for i in range(i0, i1 + 1):
        o, h, l = float(ch["o"][i]), float(ch["h"][i]), float(ch["l"][i])
        if side > 0:
            if i > i0 and o <= stop: px = o; break
            if l <= stop: px = stop; break
        else:
            if i > i0 and o >= stop: px = o; break
            if h >= stop: px = stop; break
    if px is None: px = float(ch["c"][i1])
    return D, side, side * (px - S) / S * 1e4 - COST, raw

rows, skipped = [], []
rng = random.Random(7)
avail = {}
for e in exps:
    oi = oi_at(e, "14:15")
    if not oi or f"{e} 14:30" not in bars or f"{e} 15:10" not in bars:
        skipped.append((e, "no_oi_or_bars")); continue
    med = float(np.median(list(oi.values())))
    K = max(oi, key=lambda k: (oi[k], -abs(k - float(ch["o"][bars[f"{e} 14:30"]]))))
    r_all = trade(e, K)
    avail[e] = list(oi)
    if oi[K] < DOM * med:
        skipped.append((e, "no_dominant_wall")); continue
    D, side, net, raw = r_all
    rows.append(dict(expiry=e, K=K, D=D, side=side, net=net, raw=raw))
trades = [r for r in rows if r["net"] is not None]
nets = np.array([r["net"] for r in trades])
res = dict(stage=STAGE, n_expiries=len(exps), n_events_with_wall=len(rows), n_trades=len(trades), skipped=skipped)
if len(trades) >= 3:
    m, sd = nets.mean(), nets.std(ddof=1); se = sd / math.sqrt(len(nets))
    t = m / se; p = 1 - stats.t.cdf(t, len(nets) - 1)
    ex_best = np.delete(nets, nets.argmax()).mean()
    D = np.array([r["D"] for r in rows]); R = np.array([r["raw"] for r in rows])
    beta = float(np.polyfit(D, R, 1)[0]) if len(rows) >= 3 else float("nan")
    # placebo: random available strike instead of the wall, same band/stop/cost rules
    pm = []
    for _ in range(2000):
        ns = []
        for r in rows:
            K = rng.choice(avail[r["expiry"]]); x = trade(r["expiry"], K)
            if x and x[2] is not None: ns.append(x[2])
        if ns: pm.append(np.mean(ns))
    placebo_p = float(np.mean([x >= m for x in pm]))
    res.update(mean_net=m, sd=sd, se=se, t=t, p_one_sided=p, mean_gross=m + COST, win_rate=float((nets > 0).mean()),
               mean_net_plus2bps_cost=m - 2, mean_net_excl_best=ex_best, beta_pull=beta, placebo_p=placebo_p,
               mde_80pct_bps=2.49 * se, best_trade=float(nets.max()), worst_trade=float(nets.min()),
               sum_net_bps=float(nets.sum()))
    if STAGE == "A":
        res["verdict"] = ("PASS_A" if (len(trades) >= 25 and m > 0 and p <= .07 and placebo_p <= .10 and beta > 0 and m - 2 > 0 and ex_best > 0)
                          else "REJECT" if (m <= 0 or placebo_p > .25 or beta <= 0) else "NOT_PASS_A (inconclusive)")
    else:
        res["verdict"] = ("PASS_B" if (len(trades) >= 25 and m > 0 and p <= .10 and beta > 0 and placebo_p <= .10)
                          else "UNVERIFIED" if len(trades) < 25 else "REJECT")
else:
    res["verdict"] = "UNVERIFIED (too few trades)"
os.makedirs("data/research/expiry_pin", exist_ok=True)
json.dump(res, open(f"data/research/expiry_pin/stage_{STAGE}_result.json", "w"), indent=1, default=str)
import csv
with open(f"data/research/expiry_pin/stage_{STAGE}_events.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["expiry", "K", "D", "side", "net", "raw"]); w.writeheader(); w.writerows(rows)
print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in res.items() if k != "skipped"}, indent=1)); print("skipped", skipped)
