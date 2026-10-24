"""Bid/ask parity pilot, exactly as pre-registered (data/research/option_flow/SPEC_BIDASK_PILOT_2026-10-24.md). Writes results_bidask_pilot.json."""
import importlib.util, json, os, sqlite3, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from app.candle_ai import evaluate as E, option_flow_quotes as Q

spec = importlib.util.spec_from_file_location("st", os.path.join(HERE, "scripts", "option_flow_study.py")); st = importlib.util.module_from_spec(spec); sys.argv = [sys.argv[0]]; spec.loader.exec_module(st)
MH = os.path.join(HERE, "data", "market_history.db"); IST_OFF = 19800; STEP = {"NIFTY": 50, "SENSEX": 100}; START = {"NIFTY": "2026-09-01", "SENSEX": "2026-09-03"}


def run(und):
    st.UND = und; idx = st.index_minutes(); spot = idx["c"]; base = st.baseline_frame(idx); keys = base[(base["bin_n"] >= 6)].copy()
    keys["date"] = pd.to_datetime(keys["day"] * 86400, unit="s").dt.strftime("%Y-%m-%d"); keys = keys[keys["date"] >= START[und]]
    conn = sqlite3.connect(f"file:{MH}?mode=ro", uri=True, timeout=60); rows = []; exp_cache = {}
    for r in keys.itertuples():
        d = r.date
        if d not in exp_cache: exp_cache[d] = Q.nearest_expiry(conn, und, d)
        ex = exp_cache[d]
        if not ex: continue
        t = int(r.key); s_now, s_prev = spot.get(t), spot.get(t - 900); fut = spot.get(t + 1800)
        if any(v is None or not np.isfinite(v) for v in (s_now, s_prev, fut)) or ((t + 1800 + IST_OFF) // 86400 != r.day): continue
        f = Q.parity_features(conn, und, ex, STEP[und], float(s_now), float(s_prev), t + 60)
        if f: rows.append({"date": d, "t": t, "ret_bp": (fut / s_now - 1) * 1e4, **f})
    conn.close(); return pd.DataFrame(rows)


def main():
    out = {}
    for und in ("NIFTY", "SENSEX"):
        d = run(und); y = (d["ret_bp"] > 0).astype(int).values; days = d["date"].values; res = {"points": int(len(d)), "days": int(d["date"].nunique())}
        for f in ("F3m", "F3c", "F3z"):
            ok = np.isfinite(d[f].values); a = E.auc(y[ok], d[f].values[ok]); lo, hi = E.boot_auc(y[ok], d[f].values[ok], days[ok], B=1000); res[f] = {"auc": a, "ci": [lo, hi], "n": int(ok.sum())}
        ok = np.isfinite(d["F3m"].values) & np.isfinite(d["F3c"].values); yy, dd = y[ok], days[ok]; g = pd.Series(np.arange(ok.sum())).groupby(dd).apply(lambda s: s.values).tolist(); rng = np.random.default_rng(7); diffs = []
        am, ac = d["F3m"].values[ok], d["F3c"].values[ok]
        for _ in range(1000):
            ix = np.concatenate([g[i] for i in rng.integers(0, len(g), len(g))]); diffs.append(E.auc(yy[ix], am[ix]) - E.auc(yy[ix], ac[ix]))
        res["F3m_minus_F3c"] = {"diff": res["F3m"]["auc"] - res["F3c"]["auc"], "ci": [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))]}
        q = pd.qcut(d["F3m"], 3, labels=["low", "mid", "high"]); res["tercile_mean_30m_bp"] = d.groupby(q, observed=True)["ret_bp"].mean().round(2).to_dict(); res["median_spread_w"] = float(d["spread_w"].median()); res["median_quote_age_s"] = float(d["quote_age_s"].median())
        out[und] = res; print(und, json.dumps(res, default=float), flush=True)
    sup = all(out[u]["F3m"]["auc"] >= out[u]["F3c"]["auc"] - 0.002 and out[u]["F3m"]["auc"] > 0.50 for u in out); con = all(out[u]["F3m"]["auc"] < 0.50 for u in out)
    out["label"] = "SUPPORTS" if sup else ("CONTRADICTS" if con else "INCONCLUSIVE"); print("LABEL", out["label"])
    json.dump(out, open(os.path.join(HERE, "data", "research", "option_flow", "results_bidask_pilot.json"), "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
