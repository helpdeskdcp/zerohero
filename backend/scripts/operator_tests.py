"""Pre-registered: data/research/operator_tests/SPEC_2026-10-25.md"""
import json, os, sys, sqlite3, warnings
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE); warnings.filterwarnings("ignore")
from app.option_selling.backtest import epoch
from app.scanner import stocks as S
from app.zone_breakout import data as DT
OUT = os.path.join(HERE, "data", "research", "operator_tests"); DB = os.path.join(HERE, "data", "historical", "option_flow", "option_flow.db")


def expiries():
    c = sqlite3.connect(DB); r = [(e, sorted(d.split(","))) for e, d in c.execute("SELECT expiry, days FROM done ORDER BY expiry")]; c.close(); return r


def test_a(exp, rng):
    ed = {d[-1] for _, d in exp if d[-1] >= "2024-10-03"}
    px = {s: S.load_stock(s).set_index("date")["c"] for s in S.keys()}; st = pd.DataFrame(px).sort_index()
    b = DT.load_5m("NIFTY"); ts = pd.DatetimeIndex(b["timestamp"]).tz_convert("Asia/Kolkata"); idx = pd.Series(b["close"].values, index=ts.normalize().tz_localize(None)).groupby(level=0).last()
    st.index = pd.DatetimeIndex(st.index).tz_localize(None).normalize(); j = st.join(idx.rename("NIFTY"), how="inner").loc["2024-10-01":]
    r = np.log(j).diff().dropna(); r = r.dropna(axis=1, thresh=int(0.95 * len(r))).dropna()
    X = r.drop(columns="NIFTY"); y = r["NIFTY"]; isexp = pd.Series([d.strftime("%Y-%m-%d") in ed for d in r.index], index=r.index)
    tr = (r.index < "2025-10-01") & ~isexp.values; te = r.index >= "2025-10-01"
    mu, sd = X[tr].mean(), X[tr].std(); Z = (X - mu) / sd; A = Z[tr].values; w = np.linalg.solve(A.T @ A + 1e-4 * np.eye(A.shape[1]), A.T @ (y[tr].values - y[tr].mean()))
    res = y.values - (Z.values @ w + y[tr].mean()); rt = res[te]; et = isexp.values[te]
    ratio = rt[et].std() / rt[~et].std(); r2 = 1 - rt[~et].var() / y.values[te][~et].var(); r2e = 1 - rt[et].var() / y.values[te][et].var()
    perm = []
    for _ in range(10000):
        p = rng.permutation(et); perm.append(rt[p].std() / rt[~p].std())
    perm = np.array(perm)
    return dict(n_stocks=int(X.shape[1]), n_oos=int(te.sum()), n_oos_expiry=int(et.sum()), r2_oos_nonexpiry=float(r2), r2_oos_expiry=float(r2e), resid_sd_ratio_expiry_over_non=float(ratio),
                p_ratio_gt1=float((perm >= ratio).mean()), p_ratio_lt1=float((perm <= ratio).mean()), claim_less_explained_on_expiry=bool(ratio > 1 and (perm >= ratio).mean() < 0.025))


def maxpain_and_f(c, e, days):
    d, prev = days[-1], days[-2] if len(days) > 1 else None
    if prev is None: return None
    oi = {}
    for k, o, t, v in c.execute("SELECT strike, otype, ts, oi FROM bars WHERE expiry=? AND ts BETWEEN ? AND ? AND oi>0 ORDER BY ts", (e, epoch(prev, "09:15"), epoch(prev, "15:30"))): oi[(k, o)] = v
    ks = sorted({k for k, _ in oi})
    if len(ks) < 5: return None
    pain = {K: sum(v * max(0.0, K - k) for (k, o), v in oi.items() if o == "CE") + sum(v * max(0.0, k - K) for (k, o), v in oi.items() if o == "PE") for K in ks}; kstar = min(pain, key=pain.get)
    def fwd(t):
        P = {}
        for k, o, p in c.execute("SELECT strike, otype, c FROM bars WHERE expiry=? AND ts=?", (e, t)): P[(k, o)] = p
        cand = [(abs(P[(k, "CE")] - P[(k, "PE")]), k + P[(k, "CE")] - P[(k, "PE")]) for k in {k for k, _ in P} if (k, "CE") in P and (k, "PE") in P]
        return min(cand)[1] if cand else None
    f0, f1 = fwd(epoch(d, "09:30") - 60), fwd(epoch(d, "15:15") - 60)
    if f0 is None or f1 is None: return None
    return dict(expiry=e, day=d, kstar=kstar, f0=f0, f1=f1, band=(ks[0], ks[-1]))


def pull(f0, f1, k): return (abs(f0 - k) - abs(f1 - k)) / abs(f0 - k) if abs(f0 - k) > 1e-9 else np.nan


def test_b(exp, rng):
    c = sqlite3.connect(DB); rows = [m for e, d in exp if (m := maxpain_and_f(c, e, d))]; c.close()
    f0 = np.array([r["f0"] for r in rows]); f1 = np.array([r["f1"] for r in rows]); ks = np.array([r["kstar"] for r in rows]); off = ks - f0
    obs = np.array([pull(a, b, k) for a, b, k in zip(f0, f1, ks)]); ok = ~np.isnan(obs); mean_obs = float(obs[ok].mean())
    near_obs = float((abs(f1 - ks) <= 25).mean()); mp, ns = [], []
    for _ in range(10000):
        po = rng.permutation(off); kk = f0 + po; q = np.array([pull(a, b, k) for a, b, k in zip(f0, f1, kk)]); mp.append(np.nanmean(q)); ns.append((abs(f1 - kk) <= 25).mean())
    mp, ns = np.array(mp), np.array(ns)
    return dict(n=int(ok.sum()), median_pull=float(np.median(obs[ok])), mean_pull=mean_obs, placebo_mean_pull=float(mp.mean()), placebo_97_5=float(np.percentile(mp, 97.5)), p_mean=float((mp >= mean_obs).mean()),
                share_close_within_25pts=near_obs, placebo_share=float(ns.mean()), p_share=float((ns >= near_obs).mean()), mean_abs_dist_0930=float(np.mean(abs(f0 - ks))), mean_abs_dist_1515=float(np.mean(abs(f1 - ks))),
                claim_pinning=bool(np.median(obs[ok]) > 0 and mean_obs > np.percentile(mp, 97.5)))


if __name__ == "__main__":
    rng = np.random.default_rng(11); exp = expiries(); a = test_a(exp, rng); print("A", json.dumps(a), flush=True); b = test_b(exp, rng); print("B", json.dumps(b), flush=True)
    json.dump({"A": a, "B": b}, open(os.path.join(OUT, "results.json"), "w"), indent=1)
