"""Option-flow study, exactly as pre-registered (data/research/option_flow/SPEC_2026-10-24.md). Needs data/historical/option_flow/option_flow.db (scripts/option_flow_pull.py).
Writes data/research/option_flow/results.json.   python scripts/option_flow_study.py [--max-expiries N]  (N only for a mechanics smoke test)"""
import json, os, sqlite3, sys, time
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from app.candle_ai import option_flow as OF, evaluate as E, patterns as P
OF.TARGET_MIN = int(sys.argv[sys.argv.index("--horizon") + 1]) if "--horizon" in sys.argv else 30

UND = "SENSEX" if "--underlying" in sys.argv and sys.argv[sys.argv.index("--underlying") + 1].upper() == "SENSEX" else "NIFTY"
STEP_PTS, NEAR_PTS = (100, 500) if UND == "SENSEX" else (50, 250); COST = 1.5 if UND == "SENSEX" else 1.0; SUFFIX = "_sensex" if UND == "SENSEX" else ""
HOR = int(sys.argv[sys.argv.index("--horizon") + 1]) if "--horizon" in sys.argv else 30
if HOR != 30: SUFFIX += f"_h{HOR}"
DB = os.path.join(HERE, "data", "historical", "option_flow", "option_flow_sensex.db" if UND == "SENSEX" else "option_flow.db"); MH = os.path.join(HERE, "data", "market_history.db"); IST_OFF = 19800
FEATS = ["F1", "F2", "F3", "F4", "F5"]; BASE = ["r5", "r15", "r30", "tod"]


def index_minutes():
    c = sqlite3.connect(f"file:{MH}?mode=ro", uri=True, timeout=60)
    try: d = pd.read_sql_query("SELECT bar_start, o, h, l, c FROM market_candles WHERE symbol=? AND kind='INDEX' AND tf='1m' AND session_date_ist>='2024-09-01' ORDER BY bar_start", c, params=(UND,))
    finally: c.close()
    d["ts"] = pd.to_datetime(d["bar_start"], utc=True).astype("int64") if False else (pd.to_datetime(d["bar_start"], utc=True) - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().astype(int)
    d = d.drop_duplicates("ts").set_index("ts"); d["day"] = ((d.index + IST_OFF) // 86400); return d


def baseline_frame(idx):
    """5-minute bars from the 1-minute index; r5/r15/r30 in ATR(14) units at the bar close; tod bucket. Row key = epoch of the LAST minute of the bin."""
    ist = idx.index + IST_OFF; sec_of_day = ist % 86400; binstart = idx.index - ((sec_of_day - 9 * 3600 - 15 * 60) % 300)
    g = idx.assign(bin=binstart).groupby("bin").agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), n=("c", "size"), last=("c", lambda x: x.index[-1]) if False else ("c", "size"))
    g = g[g["n"] == 5]; a = P.atr(g["h"].values, g["l"].values, g["c"].values); g["atr"] = a; g["key"] = g.index + 4 * 60
    c = g["c"]; r = {k: (c / c.shift(k) - 1) * c / g["atr"] for k in (1, 3, 6)}
    out = pd.DataFrame({"key": g["key"], "r5": r[1].values, "r15": r[3].values, "r30": r[6].values}, index=g.index)
    out["day"] = ((out["key"] + IST_OFF) // 86400); out["tod"] = (((out["key"] + IST_OFF) % 86400 - 9 * 3600 - 15 * 60) // 1800).astype(int); out["bin_n"] = out.groupby("day").cumcount()
    out["cross_day"] = out["day"] != out["day"].shift(6)                                       # r30 spans a session change -> invalid
    out.loc[out["cross_day"], ["r5", "r15", "r30"]] = np.nan; return out.reset_index(drop=True)


def build(max_expiries=None):
    idx = index_minutes(); spot = idx["c"]; base = baseline_frame(idx); c = sqlite3.connect(DB, timeout=60)
    done = pd.read_sql_query("SELECT expiry, days FROM done ORDER BY expiry", c)
    if max_expiries: done = done.head(max_expiries)
    out = []
    for e in done.itertuples():
        days = set(e.days.split(",")); bars = pd.read_sql_query("SELECT strike, otype, ts, c, v, oi FROM bars WHERE expiry=?", c, params=(e.expiry,))
        keys = base[base["day"].isin({(int((pd.Timestamp(d, tz="Asia/Kolkata") - pd.Timestamp("1970-01-01", tz="UTC")).total_seconds()) + IST_OFF) // 86400 for d in days}) & (base["bin_n"] >= 6)].copy()
        if keys.empty or bars.empty: continue
        keys["t"] = keys["key"] - 60 + 60                                          # t = epoch of the last minute's bar start of the 5m bin
        keys["t"] = keys["key"]
        fl = OF.flow_features(bars, spot, keys["t"].tolist(), STEP_PTS, NEAR_PTS); m = keys.merge(fl, on="t", how="left")
        fut = spot.reindex(m["t"] + OF.TARGET_MIN * 60).values; now = spot.reindex(m["t"]).values; m["ret_bp"] = (fut / now - 1) * 1e4
        m["same_day"] = (((m["t"] + OF.TARGET_MIN * 60 + IST_OFF) // 86400) == m["day"]).values; m.loc[~m["same_day"], "ret_bp"] = np.nan
        m["expiry"] = e.expiry; m["is_expiry_day"] = [pd.Timestamp(e.expiry).strftime("%Y-%m-%d") == pd.Timestamp(d * 86400, unit="s").strftime("%Y-%m-%d") for d in m["day"]]
        out.append(m)
        print("features", e.expiry, len(m), flush=True)
    d = pd.concat(out, ignore_index=True); d["date"] = pd.to_datetime((d["day"] * 86400), unit="s"); d["q"] = d["date"].dt.to_period("Q").astype(str)
    return d.dropna(subset=FEATS + BASE[:3] + ["ret_bp"]).reset_index(drop=True)


def fit_predict(train, test, cols, kind):
    sc = StandardScaler().fit(train[cols]); Xtr = sc.transform(train[cols]); Xte = sc.transform(test[cols]); y = (train["ret_bp"] > 0).astype(int).values
    k = int(len(train) * 0.8)
    if kind == "logit":
        m = LogisticRegression(C=1.0, max_iter=500).fit(Xtr, y); return m.predict_proba(Xte)[:, 1], m.predict_proba(Xtr[k:])[:, 1]
    m = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200, min_samples_leaf=100, l2_regularization=1.0, random_state=0).fit(Xtr[:k], y[:k]); return m.predict_proba(Xte)[:, 1], m.predict_proba(Xtr[k:])[:, 1]


def day_boot_diff(y, p_a, p_b, days, B=1000, seed=7):
    g = pd.Series(np.arange(len(y))).groupby(days).apply(lambda s: s.values).tolist(); rng = np.random.default_rng(seed); out = []
    for _ in range(B):
        ix = np.concatenate([g[i] for i in rng.integers(0, len(g), len(g))]); out.append(E.auc(y[ix], p_a[ix]) - E.auc(y[ix], p_b[ix]))
    return np.percentile(out, [1.25, 98.75])


def main():
    mx = int(sys.argv[sys.argv.index("--max-expiries") + 1]) if "--max-expiries" in sys.argv else None
    d = build(mx); qs = sorted(d["q"].unique()); res = {"rows": int(len(d)), "quarters": qs, "univariate_auc": {f: E.auc((d.ret_bp > 0).astype(int).values, d[f].values) for f in FEATS}, "models": {}}
    print("rows", len(d), "quarters", qs, flush=True)
    preds = {k: [] for k in ("B", "F", "G")}; meta = []; thr = {k: [] for k in preds}
    for i, q in enumerate(qs):
        if i < 2: continue
        tr = d[d["q"].isin(qs[:i])]; te = d[d["q"] == q]
        if len(te) < 200: continue
        pb, cb = fit_predict(tr, te, BASE, "logit"); pf, cf = fit_predict(tr, te, BASE + FEATS, "logit"); pg, cg = fit_predict(tr, te, BASE + FEATS, "hgb")
        for k, p, cal in (("B", pb, cb), ("F", pf, cf), ("G", pg, cg)): preds[k].append(p); thr[k].append(np.full(len(te), np.quantile(np.abs(cal - 0.5), 0.90)))
        meta.append(te[["q", "date", "ret_bp", "is_expiry_day"]])
    m = pd.concat(meta, ignore_index=True); y = (m["ret_bp"] > 0).astype(int).values; days = m["date"].dt.strftime("%Y-%m-%d").values
    P_ = {k: np.concatenate(v) for k, v in preds.items()}; T_ = {k: np.concatenate(v) for k, v in thr.items()}
    base_auc = E.auc(y, P_["B"]); res["test_rows"] = int(len(m)); res["test_quarters"] = sorted(m["q"].unique()); res["auc_baseline"] = base_auc
    for k, nm in (("F", "logit_flow"), ("G", "hgb_flow")):
        auc = E.auc(y, P_[k]); lo, hi = E.boot_auc(y, P_[k], days, B=1000); dl = day_boot_diff(y, P_[k], P_["B"], days)
        sig = np.abs(P_[k] - 0.5) > T_[k]; dirret = np.sign(P_[k][sig] - 0.5) * m["ret_bp"].values[sig]; net = dirret - COST
        mean_net = float(net.mean()) if sig.sum() else float("nan"); ci = E.boot_mean(net, days[sig], B=1000) if sig.sum() >= 30 else (float("nan"), float("nan"))
        per_q = {q: E.auc(y[(m["q"] == q).values], P_[k][(m["q"] == q).values]) for q in res["test_quarters"]}
        g = {"G1": bool(auc >= 0.53 and lo > 0.50), "G2": bool((auc - base_auc) >= 0.010 and dl[0] > 0), "G3": bool(sig.sum() >= 300 and mean_net > 0 and ci[0] > 0), "G4": bool(sum(v > 0.5 for v in per_q.values()) >= 5)}
        res["models"][nm] = {"auc": auc, "auc_ci": [lo, hi], "lift_vs_baseline": auc - base_auc, "lift_ci": [float(dl[0]), float(dl[1])], "signals": int(sig.sum()), "gross_bp": float(dirret.mean()) if sig.sum() else None, "net_bp": mean_net, "net_ci": [float(ci[0]), float(ci[1])],
                             "auc_by_quarter": per_q, "gates": g, "all": all(g.values())}
        e0 = m["is_expiry_day"].values; res["models"][nm]["auc_expiry_day"] = E.auc(y[e0], P_[k][e0]) if e0.sum() > 200 else None; res["models"][nm]["auc_other_days"] = E.auc(y[~e0], P_[k][~e0])
    enough = res["models"]["logit_flow"]["signals"] >= 300 and len(res["test_quarters"]) >= 5
    res["verdict"] = "INSUFFICIENT_SAMPLE" if not enough else ("GO-SHADOW" if any(v["all"] for v in res["models"].values()) else "NO-GO")
    name = ("results_smoke" if mx else "results") + SUFFIX + ".json"; json.dump(res, open(os.path.join(HERE, "data", "research", "option_flow", name), "w"), indent=1, default=float)
    print(json.dumps({k: v for k, v in res.items() if k != "models"}, default=float)[:900]); print(json.dumps(res["models"], default=float)[:2400])


if __name__ == "__main__":
    main()
