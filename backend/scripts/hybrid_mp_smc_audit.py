"""Phase 5: baseline vs each SMC component individually vs full SMC, identical data/costs, plus regime breakdown. Nothing is tuned.
Baseline = fixed_params (SMC off). Cost = 2 bps round trip. Data = NIFTY INDEX 1m (research cache), chart 5m."""
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import fixed_params, resample, run  # noqa: E402
from app.research_engines.hybrid_mp.smc import smc_context  # noqa: E402
from app.research_engines.smart_bos.data import ist_minutes, load_1m  # noqa: E402
from app.research_engines.smart_bos.sim import summarize  # noqa: E402
from app.research_engines.smart_bos.splits import OOS, TRAIN, VALID, of_split  # noqa: E402

COMP = ("sweep", "inducement", "displacement", "fvg_or_ob_touch", "discount_premium")
b1 = load_1m("NIFTY", "INDEX")
chart = resample(b1, 5, 555, 930)
n = len(chart["t"])
days, _ = ist_minutes(chart["t"])


def stats(tr, cost):
    s = summarize(tr, cost_bps=cost, boot=500)
    if not s.get("n"):
        return dict(n=0)
    r = np.array([t.r - cost * t.entry / 1e4 / t.risk for t in tr])
    return dict(n=s["n"], mean=s["mean_r"], lo=s["ci95_r"][0], hi=s["ci95_r"][1], win=s["win"], dd=s["max_dd_r"],
                avg_win=float(r[r > 0].mean()) if (r > 0).any() else 0.0, avg_loss=float(r[r <= 0].mean()) if (r <= 0).any() else 0.0)


def line(label, tr):
    out = f"{label:34s}"
    for name, sp in (("TRAIN", TRAIN), ("VALID", VALID), ("OOS", OOS)):
        sub = of_split(tr, sp)
        g, nn = stats(sub, 0), stats(sub, 2)
        out += f" | {name} n={g['n']:5d} gross={g.get('mean', 0):+.3f}({g.get('lo', 0):+.2f},{g.get('hi', 0):+.2f}) net={nn.get('mean', 0):+.3f} win={g.get('win', 0)*100:4.1f}% dd={nn.get('dd', 0):6.1f}"
    return out


runs = [("BASELINE (SMC off)", {})]
runs += [(f"+ only [{c}]", dict(smc_filter=True, smc_require=(c,))) for c in COMP]
runs += [("+ score >= 2 of 5", dict(smc_filter=True, smc_min=2)), ("+ score >= 3 of 5", dict(smc_filter=True, smc_min=3)),
         ("+ FULL: all 5 components", dict(smc_filter=True, smc_require=COMP))]
results = {}
for label, kw in runs:
    tr, st = run(chart, fixed_params(chart_tf=5, **kw), b1=b1)
    results[label] = tr
    print(line(label, tr), flush=True)

# ---- regimes (causal labels at the signal bar), baseline vs score>=2 vs full ----
atr = np.full(n, np.nan)
h, l, c = chart["h"], chart["l"], chart["c"]
tr_ = np.maximum.reduce([h[1:] - l[1:], abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])])
a = np.convolve(tr_, np.ones(14) / 14, mode="valid")
atr[14:14 + len(a)] = a
# volatility regime: ATR relative to the median ATR of the previous 20 sessions (strictly earlier days)
ud = np.unique(days)
med_prev = {}
for k, d in enumerate(ud):
    if k >= 20:
        prev = np.isin(days, ud[k - 20:k])
        med_prev[d] = np.nanmedian(atr[prev])
dclose = {d: c[days == d][-1] for d in ud}
def regimes(t):
    d = days[t.sig_i]
    k = int(np.searchsorted(ud, d))
    vr = atr[t.sig_i] / med_prev[d] if d in med_prev and not np.isnan(atr[t.sig_i]) else np.nan
    vol = "n/a" if np.isnan(vr) else ("LOW vol" if vr < 0.85 else "HIGH vol" if vr > 1.2 else "MID vol")
    if k >= 20:
        sma = np.mean([dclose[x] for x in ud[k - 20:k]])
        trend = "above 20d mean" if dclose[ud[k - 1]] > sma else "below 20d mean"
    else:
        trend = "n/a"
    aligned = "with-trend" if trend != "n/a" and ((t.side == 1) == (trend == "above 20d mean")) else ("counter-trend" if trend != "n/a" else "n/a")
    return vol, aligned
print("\nREGIME BREAKDOWN (all periods; gross | net 2 bps; n)")
for label in ("BASELINE (SMC off)", "+ score >= 2 of 5", "+ FULL: all 5 components"):
    tr = results[label]
    lab = [regimes(t) for t in tr]
    for dim, idx, keys in (("volatility", 0, ("LOW vol", "MID vol", "HIGH vol")), ("trade vs 20d trend", 1, ("with-trend", "counter-trend"))):
        for kname in keys:
            sub = [t for t, r in zip(tr, lab) if r[idx] == kname]
            if len(sub) < 30:
                print(f"  {label:28s} {dim:18s} {kname:14s} n={len(sub)} (too few)")
                continue
            g, nn = stats(sub, 0), stats(sub, 2)
            print(f"  {label:28s} {dim:18s} {kname:14s} n={g['n']:5d} gross={g['mean']:+.3f} ({g['lo']:+.3f},{g['hi']:+.3f}) net={nn['mean']:+.3f} win={g['win']*100:4.1f}%")
by = {}
for label in ("BASELINE (SMC off)", "+ score >= 2 of 5"):
    by[label] = {}
    for t in results[label]:
        by[label].setdefault(dt.date.fromordinal(719163 + t.day).year, []).append(t)
print("\nBY YEAR (net 2 bps meanR): " + " | ".join(f"{y}: base {stats(by['BASELINE (SMC off)'].get(y, []), 2).get('mean', float('nan')):+.2f} / smc2 {stats(by['+ score >= 2 of 5'].get(y, []), 2).get('mean', float('nan')):+.2f}" for y in sorted(by['BASELINE (SMC off)'])))
