"""Walk-forward evaluation, calibration metrics, day-block bootstrap and the pre-registered gates (see data/research/candle_ai/SPEC_2026-10-24.md)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import binomtest, rankdata

from . import features as F
from .models import Baseline, EBTable, GBM

COST_BPS = 1.0
RNG = np.random.default_rng(7)


def auc(y, p) -> float:
    y = np.asarray(y); n1 = y.sum(); n0 = len(y) - n1
    if n1 == 0 or n0 == 0: return float("nan")
    r = rankdata(p); return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def reliability(p, y, bins=10):
    p = np.asarray(p); y = np.asarray(y); b = np.minimum((p * bins).astype(int), bins - 1); err = 0.0
    for k in range(bins):
        m = b == k
        if m.any(): err += m.sum() * abs(y[m].mean() - p[m].mean())
    lp = np.log(np.clip(p, 1e-4, 1 - 1e-4) / (1 - np.clip(p, 1e-4, 1 - 1e-4)))
    from sklearn.linear_model import LogisticRegression
    slope = float(LogisticRegression(C=1e6, max_iter=500).fit(lp.reshape(-1, 1), y).coef_[0][0]) if 0 < y.sum() < len(y) else float("nan")
    return float(err / len(p)), slope


def _day_groups(dates):
    codes, uniq = pd.factorize(pd.Series(dates)); return [np.where(codes == i)[0] for i in range(len(uniq))]


def boot_auc(y, p, dates, B=200):
    g = _day_groups(dates); out = []
    for _ in range(B):
        idx = np.concatenate([g[i] for i in RNG.integers(0, len(g), len(g))]); out.append(auc(y[idx], p[idx]))
    return float(np.nanpercentile(out, 2.5)), float(np.nanpercentile(out, 97.5))


def boot_mean(vals, dates, B=400):
    g = _day_groups(dates); s = np.array([vals[i].sum() for i in g]); n = np.array([len(i) for i in g]); out = []
    for _ in range(B):
        k = RNG.integers(0, len(g), len(g)); out.append(s[k].sum() / max(n[k].sum(), 1))
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def walk_forward(f: pd.DataFrame, tgt: pd.DataFrame, years, with_volume=False, min_train=20000) -> pd.DataFrame:
    cols = F.model_columns(f, with_volume); y_all = tgt["y"].values; yr = pd.DatetimeIndex(f["date"]).year.values; date = pd.DatetimeIndex(f["date"])
    res = []
    for Y in years:
        te = (yr == Y) & ~np.isnan(y_all)
        tr = (date < pd.Timestamp(f"{Y}-01-01") - pd.Timedelta(days=1)) & ~np.isnan(y_all)               # 1-day embargo
        if te.sum() < 500 or tr.sum() < min_train: continue
        ftr, ytr = f[tr], y_all[tr].astype(int); fte = f[te]
        m1 = EBTable().fit(ftr, ytr); m2 = GBM(cols).fit(ftr, ytr); b1 = Baseline().fit(ftr, ytr)
        thr = float(np.quantile(np.abs(m2.cal_p - 0.5), 0.90))                                           # signal threshold fixed on TRAIN-calibration predictions only
        r = pd.DataFrame({"date": fte["date"].values, "year": Y, "y": y_all[te], "ret_bps": tgt["ret_bps"].values[te], "p0": ytr.mean(), "p1": m1.predict(fte), "p2": m2.predict(fte), "pb": b1.predict(fte), "thr": thr})
        res.append(r)
    return pd.concat(res, ignore_index=True) if res else pd.DataFrame()


def cell_metrics(r: pd.DataFrame, cost=COST_BPS) -> dict:
    y = r["y"].values.astype(int); out = {"n": int(len(r)), "base": float(y.mean())}
    for k in ("p1", "p2", "pb"): out[f"auc_{k}"] = auc(y, r[k].values)
    out["auc2_ci"] = boot_auc(y, r["p2"].values, r["date"].values)
    out["rel_err"], out["slope"] = reliability(r["p2"].values, y)
    sig = np.abs(r["p2"].values - 0.5) > r["thr"].values; out["n_signals"] = int(sig.sum())
    if sig.sum() >= 30:
        d = np.sign(r["p2"].values[sig] - 0.5) * r["ret_bps"].values[sig]; out["hit_signals"] = float((d > 0).mean())
        out["gross_bps"] = float(d.mean()); out["net_bps"] = float(d.mean() - cost); lo, hi = boot_mean(d, r["date"].values[sig]); out["net_ci"] = (lo - cost, hi - cost)
        out["net_bps_0.5"] = float(d.mean() - 0.5); out["net_bps_2"] = float(d.mean() - 2.0)
    return out


def gates(c: dict) -> dict:
    g = {"G1": bool(c["rel_err"] <= 0.03 and 0.8 <= c["slope"] <= 1.2), "G2": bool(c["auc_p2"] >= 0.52 and c["auc2_ci"][0] > 0.50),
         "G3": bool(c.get("net_ci") and c["net_bps"] > 0 and c["net_ci"][0] > 0), "G4": bool(c["auc_p2"] >= c["auc_pb"] + 0.005 and c["auc_p2"] >= c["auc_p1"] + 0.005)}
    g["enough"] = bool(c["n"] >= 5000 and c["n_signals"] >= 500); g["all"] = all(g[k] for k in ("G1", "G2", "G3", "G4")); return g


def verdict(cells: dict) -> str:
    if not all(v["gates"]["enough"] for v in cells.values()): return "INCONCLUSIVE"
    return "GO-SHADOW" if all(v["gates"]["all"] for v in cells.values()) else "NO-GO"


def cell_table(f: pd.DataFrame, tgt: pd.DataFrame, discovery: tuple, replication: tuple, min_n=200, cost=COST_BPS, fdr=0.10) -> list[dict]:
    """Pattern x level-context x trend cells: discovery on one period (BH-FDR), kept only if the SAME sign replicates at p<0.05 in the other."""
    from . import patterns as P
    d = pd.DatetimeIndex(f["date"]); ok = ~np.isnan(tgt["y"].values)
    def sel(rng): return ok & (d >= pd.Timestamp(rng[0])) & (d < pd.Timestamp(rng[1]))
    def stats(mask):
        sub = f[mask & (f["dom"].values >= 0)]; y = tgt["y"].values[mask & (f["dom"].values >= 0)]; r = tgt["ret_bps"].values[mask & (f["dom"].values >= 0)]
        base = float(tgt["y"].values[mask].mean()); key = list(zip(sub["dom"], sub["near_fam"], sub["near_side"], sub["near_zone"], sub["trend"]))
        agg = {}
        for k, yy, rr in zip(key, y, r): a = agg.setdefault(k, [0, 0, 0.0]); a[0] += 1; a[1] += yy; a[2] += rr
        return base, agg
    b1, a1 = stats(sel(discovery)); b2, a2 = stats(sel(replication)); rows = []
    for k, (n, ups, sr) in a1.items():
        if n < min_n: continue
        p = binomtest(int(ups), n, b1).pvalue; rows.append([k, n, ups / n - b1, p, sr / n])
    if not rows: return []
    ps = np.array([r[3] for r in rows]); order = np.argsort(ps); m = len(ps); adj = np.empty(m); prev = 1.0
    for rank, i in zip(range(m, 0, -1), order[::-1]): prev = min(prev, ps[i] * m / rank); adj[i] = prev
    out = []
    for r, q in zip(rows, adj):
        if q > fdr or r[0] not in a2 or a2[r[0]][0] < 100: continue
        n2, u2, s2 = a2[r[0]]; p2 = binomtest(int(u2), n2, b2).pvalue; e1, e2 = r[2], u2 / n2 - b2
        if np.sign(e1) != np.sign(e2) or p2 >= 0.05: continue
        dirn = 1 if e2 > 0 else -1
        out.append({"pattern": P.PRIORITY[r[0][0]], "level_family": r[0][1], "side": int(r[0][2]), "zone": r[0][3], "trend": int(r[0][4]), "n_disc": r[1], "edge_disc": float(e1), "q_disc": float(q), "n_repl": n2,
                    "edge_repl": float(e2), "p_repl": float(p2), "dir": dirn, "gross_bps_repl": float(dirn * s2 / n2), "net_bps_repl": float(dirn * s2 / n2 - cost)})
    return sorted(out, key=lambda x: -x["net_bps_repl"])
