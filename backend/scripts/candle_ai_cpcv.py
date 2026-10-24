"""CPCV re-check, as pre-registered in data/research/candle_ai/ADDENDUM_CPCV_2026-10-24.md.  Writes results_cpcv.json."""
import json, os, sys, time
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from scipy.stats import kurtosis, norm, skew
from skfolio.model_selection import CombinatorialPurgedCV
from app.index_signal_research.data import load_index_5m
from app.candle_ai import evaluate as E, features as F
from app.candle_ai.models import Baseline, GBM

COST = 1.0; N_TRIALS = 10; EULER = 0.5772156649


def dsr(daily, sr_var, n_trials=N_TRIALS):
    daily = np.asarray(daily, float); T = len(daily); sd = daily.std(ddof=1)
    if T < 30 or sd == 0: return float("nan"), float("nan")
    sr = daily.mean() / sd; sr0 = np.sqrt(max(sr_var, 1e-12)) * ((1 - EULER) * norm.ppf(1 - 1 / n_trials) + EULER * norm.ppf(1 - 1 / (n_trials * np.e)))
    den = np.sqrt(max(1 - skew(daily) * sr + (kurtosis(daily, fisher=False) - 1) / 4 * sr ** 2, 1e-9))
    return float(sr), float(norm.cdf((sr - sr0) * np.sqrt(T - 1) / den))


def run(sym, log):
    df = load_index_5m(sym); f = F.build(df); tg = F.target(df); ok = ~np.isnan(tg["y"].values)
    f = f[ok].reset_index(drop=True); y = tg["y"].values[ok].astype(int); ret = tg["ret_bps"].values[ok]; cols = F.model_columns(f, False)
    cv = CombinatorialPurgedCV(n_folds=10, n_test_folds=2, purged_size=150, embargo_size=75); pid = cv.get_path_ids(); paths = {}
    t0 = time.time()
    for s, (tr, tests) in enumerate(cv.split(f.values)):
        ftr = f.iloc[tr]; ytr = y[tr]; m2 = GBM(cols).fit(ftr, ytr); b1 = Baseline().fit(ftr, ytr); thr = float(np.quantile(np.abs(m2.cal_p - 0.5), 0.90))
        for k, te in enumerate(tests):
            fte = f.iloc[te]; paths.setdefault(int(pid[s][k]), []).append(pd.DataFrame({"idx": te, "date": fte["date"].values, "y": y[te], "ret": ret[te], "p2": m2.predict(fte), "pb": b1.predict(fte), "thr": thr}))
        if s % 5 == 0: log(f"{sym} split {s + 1}/45 {round(time.time() - t0)}s")
    rows = []
    for p in sorted(paths):
        r = pd.concat(paths[p]).sort_values("idx"); sig = np.abs(r.p2 - 0.5) > r.thr
        d = np.sign(r.p2[sig] - 0.5) * r.ret[sig]; net = d - COST; days = pd.DatetimeIndex(r.date)
        daily = pd.Series(net.values, index=days[sig.values]).groupby(level=0).sum().reindex(pd.DatetimeIndex(sorted(set(days))), fill_value=0.0)
        lo, hi = E.boot_mean(net.values, r.date.values[sig.values])
        rows.append({"path": p, "n": int(len(r)), "auc_m2": E.auc(r.y.values, r.p2.values), "auc_b1": E.auc(r.y.values, r.pb.values), "signals": int(sig.sum()), "hit": float((d > 0).mean()), "gross_bps": float(d.mean()), "net_bps": float(net.mean()), "net_ci": (lo, hi), "daily": daily.values})
    var_sr = float(np.var([r["daily"].mean() / r["daily"].std(ddof=1) for r in rows], ddof=1))
    for r in rows: r["sr_daily"], r["dsr"] = dsr(r.pop("daily"), var_sr)
    return rows


def main():
    out = {}; log = lambda m: print(m, flush=True)
    for sym in ("NIFTY", "BANKNIFTY"):
        rows = run(sym, log); au = np.array([r["auc_m2"] for r in rows]); nb = np.array([r["net_bps"] for r in rows]); ds = np.array([r["dsr"] for r in rows])
        med = sorted(rows, key=lambda r: r["net_bps"])[len(rows) // 2]
        g = {"A1": bool((au > 0.5).sum() >= 8 and np.median(au) >= 0.52), "A2": bool((nb > 0).all() and med["net_ci"][0] > 0), "A3": bool(np.nanmedian(ds) >= 0.95)}
        out[sym] = {"paths": rows, "median_path": med["path"], "auc_median": float(np.median(au)), "net_bps_median": float(np.median(nb)), "dsr_median": float(np.nanmedian(ds)), "gates": g, "pass": all(g.values())}
        log(f"{sym}: AUC paths {np.round(au, 4).tolist()} median {np.median(au):.4f} | net@1bp paths {np.round(nb, 2).tolist()} | DSR {np.round(ds, 3).tolist()} | gates {g}")
    out["verdict"] = "CPCV ROBUST (shadow-worthy only)" if all(out[s]["pass"] for s in ("NIFTY", "BANKNIFTY")) else "CPCV CONFIRMS NO-GO"
    log("VERDICT " + out["verdict"]); json.dump(out, open(os.path.join(HERE, "data", "research", "candle_ai", "results_cpcv.json"), "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
