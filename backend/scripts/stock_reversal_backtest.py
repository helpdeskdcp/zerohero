"""Pre-registered test: data/research/stock_reversal/SPEC_2026-10-25.md."""
import json, os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
from app.scanner import stocks as S


def closes():
    ks = S.keys(); out = {}
    for s in S.UNIVERSE:
        if s in ks:
            d = S.load_stock(s); out[s] = d.set_index("date")["c"]
    return pd.DataFrame(out).sort_index()


def run(px, L, H, c_bps, q=0.2):
    px = px.copy(); r1 = px.pct_change(); bad = (r1.abs() > 0.30)          # unadjusted corporate-action artefacts: whole window of that stock excluded
    rows = []; i = L
    while i + 1 + H < len(px):
        sig = px.iloc[i] / px.iloc[i - L] - 1; e, x = i + 1, i + 1 + H
        ret = px.iloc[x] / px.iloc[e] - 1
        ok = sig.notna() & ret.notna() & ~bad.iloc[i - L + 1:x + 1].any()
        if ok.sum() >= 30:
            s = sig[ok].sort_values(); k = max(2, int(round(len(s) * q))); lo, hi = s.index[:k], s.index[-k:]
            lg, sh, bm = ret[lo].mean(), ret[hi].mean(), ret[ok].mean()
            rows.append(dict(date=str(px.index[i].date()), long=lg, short=sh, bench=bm, gross=lg - sh, net=lg - sh - 2 * c_bps / 1e4))
        i += H
    return pd.DataFrame(rows)


def verdict(df, H, level=0.025, nmin=100, seed=3):
    n = len(df); x = df["net"].values
    rng = np.random.default_rng(seed); B = 4; blocks = [x[j:j + B] for j in range(0, n, B)]
    bm = [np.concatenate([blocks[t] for t in rng.integers(0, len(blocks), len(blocks))]).mean() for _ in range(4000)]
    lo, hi = np.percentile(bm, [100 * level / 2, 100 * (1 - level / 2)]); h = n // 2
    yrs = df.assign(y=df["date"].str[:4]).groupby("y")["net"].mean(); eq = np.cumsum(x)
    ok = n >= nmin and x.mean() > 0 and lo > 0 and x[:h].mean() > 0 and x[h:].mean() > 0 and (yrs > 0).sum() >= 5
    return dict(n=n, mean_net=float(x.mean()), mean_gross=float(df["gross"].mean()), ci=[float(lo), float(hi)], half1=float(x[:h].mean()), half2=float(x[h:].mean()),
                years_pos=f"{int((yrs > 0).sum())}/{len(yrs)}", long_excess_vs_basket=float((df["long"] - df["bench"]).mean()), short_excess_vs_basket=float((df["bench"] - df["short"]).mean()),
                hit=float((x > 0).mean()), worst=float(x.min()), maxdd=float((eq - np.maximum.accumulate(eq)).min()), verdict="GO-SHADOW" if ok else ("INSUFFICIENT_SAMPLE" if n < nmin else "NO-GO"),
                years={k: round(float(v), 4) for k, v in yrs.items()})


if __name__ == "__main__":
    px = closes(); print("stocks", px.shape[1], "days", len(px), px.index[0].date(), px.index[-1].date(), flush=True); res = {}
    for L, H, nmin in ((5, 5, 100), (21, 21, 40)):
        df = run(px, L, H, 10); v = verdict(df, H, nmin=nmin); v["net_at_20bps"] = float(run(px, L, H, 20)["net"].mean()); res[f"L{L}_H{H}"] = v; print(f"L{L}H{H}", json.dumps(v, indent=None), flush=True)
    json.dump(res, open(os.path.join(HERE, "data", "research", "stock_reversal", "results.json"), "w"), indent=1)
