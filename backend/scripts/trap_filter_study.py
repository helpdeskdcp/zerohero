"""Pre-registered walk-forward trap-filter study: data/research/trap_filter/SPEC_2026-10-25.md."""
import json, os, sys, warnings
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE); warnings.filterwarnings("ignore")
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from app.zone_breakout import backtest as B, data as DT, trap_features as TF

THR = 0.42; YEARS = list(range(2019, 2027)); OUT = os.path.join(HERE, "data", "research", "trap_filter")


def dataset():
    p = os.path.join(OUT, "dataset.pkl")
    if os.path.exists(p): return pd.read_pickle(p)
    fr = []
    for s in ("NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY", "BANKEX"):
        bars = DT.load_5m(s); t = B.run(s, 5, bars=bars)
        if len(t): fr.append(TF.build(bars, t))
        print(s, len(t), flush=True)
    d = pd.concat(fr, ignore_index=True); d["win"] = (d["net_R"] > 0).astype(int); d.to_pickle(p); return d


def models():
    return {"logit": lambda: make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=0.5, max_iter=500)),
            "hgb": lambda: HistGradientBoostingClassifier(max_depth=3, max_iter=100, min_samples_leaf=50, l2_regularization=1.0, random_state=0)}


def walk(d, mk, shuffle_seed=None):
    parts = []
    for Y in YEARS:
        tr, te = d[d["year"] < Y], d[d["year"] == Y]
        if len(te) == 0 or len(tr) < 300: continue
        y = tr["win"].values
        if shuffle_seed is not None: y = np.random.default_rng(shuffle_seed + Y).permutation(y)
        m = mk().fit(tr[TF.FEATURES].values, y); te = te.copy(); te["p"] = m.predict_proba(te[TF.FEATURES].values)[:, 1]; parts.append(te)
    return pd.concat(parts)


def evaluate(oos):
    auc = float(roc_auc_score(oos["win"], oos["p"])); tk = oos[oos["p"] >= THR]; ys = tk.groupby("year")["net_R"].mean()
    late = tk[tk["year"] >= 2025]
    base = float(oos["net_R"].mean()); mean = float(tk["net_R"].mean()) if len(tk) else float("nan"); lo = B.boot_lo(tk, 0.025) if len(tk) >= 30 else float("nan")
    ok = auc >= 0.55 and len(tk) >= 300 and mean > 0 and lo > 0 and mean - base >= 0.10 and (ys > 0).sum() >= 5 and len(late) > 0 and late["net_R"].mean() > 0
    return dict(auc=auc, oos_n=int(len(oos)), baseline_net_R=base, taken_n=int(len(tk)), coverage=float(len(tk) / len(oos)), taken_net_R=mean, taken_gross_R=float(tk["R"].mean()) if len(tk) else None,
                taken_win=float((tk["R"] > 0).mean()) if len(tk) else None, ci_lo=lo, years_pos=f"{int((ys > 0).sum())}/{len(ys)}", late_2025_26=float(late["net_R"].mean()) if len(late) else None,
                by_year={int(k): round(float(v), 3) for k, v in ys.items()}, verdict="GO-SHADOW" if ok else "NO-GO")


if __name__ == "__main__":
    d = dataset(); print("trades", len(d), "win", d["win"].mean(), flush=True); res = {}
    for name, mk in models().items():
        oos = walk(d, mk); res[name] = evaluate(oos); print(name, json.dumps(res[name]), flush=True)
        perm = [evaluate(walk(d, mk, shuffle_seed=100 * s)) for s in range(20)]
        res[name]["permutation_auc_mean"] = float(np.mean([p["auc"] for p in perm])); res[name]["permutation_taken_net_R_mean"] = float(np.nanmean([p["taken_net_R"] for p in perm])); res[name]["permutation_taken_n_mean"] = float(np.mean([p["taken_n"] for p in perm]))
        print(name, "perm", res[name]["permutation_auc_mean"], res[name]["permutation_taken_net_R_mean"], flush=True)
        if name == "hgb":
            from sklearn.inspection import permutation_importance
            m = mk().fit(d[d["year"] < 2026][TF.FEATURES].values, d[d["year"] < 2026]["win"].values); te = d[d["year"] == 2026]
            if len(te) > 50:
                pi = permutation_importance(m, te[TF.FEATURES].values, te["win"].values, n_repeats=5, random_state=0, scoring="roc_auc"); res["hgb_importance_2026"] = dict(sorted(zip(TF.FEATURES, map(float, pi.importances_mean)), key=lambda x: -x[1])[:8])
    json.dump(res, open(os.path.join(OUT, "results.json"), "w"), indent=1)
