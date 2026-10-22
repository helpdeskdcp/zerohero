"""Addendum to SPEC_2026-10-23 (written after the first result, labelled as such): (a) the spec's fix for MISCALIBRATED = one monotone map fitted on P1 (pooled instruments), checked on P2;
(b) structural-level touch rate vs the OBSERVED plain-level touch rate at the same distance (the first lift used the biased model prediction, so it is confounded by the model's own bias)."""
import json, numpy as np, pandas as pd
import run_study as R
tabs = {}
for inst, (path, kind) in R.FILES.items():
    d = R.load(kind, path); tabs[inst] = R.study(d)
P1 = pd.concat([p[(p.day >= "2015-01-01") & (p.day <= "2019-12-31")] for p, _ in tabs.values()])
mp = P1.groupby("d").agg(p=("p", "first"), obs=("y", "mean")).reset_index().sort_values("d")
obs = np.maximum.accumulate(mp.obs.values[::-1])[::-1]               # monotone non-increasing in d
m = dict(zip(mp.d.round(2), obs)); print("map d->p_hat->recal:", [(r.d, round(r.p, 4), round(o, 4)) for r, o in zip(mp.itertuples(), obs)])
out = {"map": {str(k): round(float(v), 4) for k, v in m.items()}}
for inst, (probe, struct) in tabs.items():
    for per, (a, b) in R.PERIODS.items():
        pp = probe[(probe.day >= a) & (probe.day <= b)].copy(); pp["p"] = pp.d.round(2).map(m)
        c = R.calib(pp); ok = c["reliability_error"] <= 0.03 and 0.8 <= c["slope"] <= 1.2
        ss = struct[(struct.day >= a) & (struct.day <= b)].copy()
        plain = pp.groupby("d").y.mean()                                     # observed plain touch rate by probe distance
        xs = np.array(sorted(plain.index)); ys = np.array([plain[x] for x in xs]); ss["pl"] = np.interp(ss.d.values, xs, ys)      # observed plain touch rate at the SAME distance (linear interpolation between probe distances)
        ss["diff"] = ss.y - ss.pl
        agg = ss.assign(k="ALL").groupby(["kind"]).apply(lambda g: pd.Series({"n": len(g), "lift_vs_plain": g["diff"].mean()}), include_groups=False)
        out[f"{inst}_{per}"] = {"recal_rel_err": c["reliability_error"], "recal_slope": c["slope"], "ok": bool(ok), "structural_vs_plain": {k: [int(v.n), round(float(v.lift_vs_plain), 4)] for k, v in agg.iterrows()}, "all": round(float(ss["diff"].mean()), 4)}
        print(inst, per, "recal rel_err", c["reliability_error"], "slope", c["slope"], "OK" if ok else "FAIL", "| structural-vs-plain", out[f"{inst}_{per}"]["all"], {k: v[1] for k, v in out[f"{inst}_{per}"]["structural_vs_plain"].items()})
json.dump(out, open("addendum_results.json", "w"), indent=1)
