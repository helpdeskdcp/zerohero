"""HMM/GARCH regime layer study, exactly as pre-registered in data/research/candle_ai/ADDENDUM_REGIME_2026-10-24.md. Writes results_regime.json."""
import json, os, sys, time
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from scipy.stats import spearmanr
from app.index_signal_research.data import load_index_5m
from app.candle_ai import evaluate as E, features as F
from app.candle_ai.models import GBM
from app.candle_ai.regime import GarchRegime, HMMRegime, regime_frame

COST = 1.0; P1, P2 = list(range(2018, 2022)), list(range(2022, 2027)); REG = ["sigma_next", "sigma_ratio", "hmm_p0", "hmm_p1", "hmm_p2"]


def study(sym, log):
    df = load_index_5m(sym); f = F.build(df); tg = F.target(df); y_all = tg["y"].values; yr = pd.DatetimeIndex(f["date"]).year.values; date = pd.DatetimeIndex(f["date"]); res = []
    for Y in P1 + P2:
        t0 = time.time(); te = (yr == Y) & ~np.isnan(y_all); tr = (date < pd.Timestamp(f"{Y}-01-01") - pd.Timedelta(days=1)) & ~np.isnan(y_all)
        upto = np.where(yr <= Y)[0]; sub = df.iloc[upto].reset_index(drop=True)
        trspan = df[np.asarray(date < pd.Timestamp(f"{Y}-01-01") - pd.Timedelta(days=1))].reset_index(drop=True)
        g = GarchRegime().fit(trspan); h = HMMRegime().fit(trspan); rg = regime_frame(sub, g, h); rg.index = upto
        fx = f.iloc[upto].copy()
        for c in REG + ["hmm_state"]: fx[c] = rg[c].values
        trm = tr[upto]; tem = te[upto]; ytr = y_all[upto][trm].astype(int); ftr = fx[trm]; fte = fx[tem]
        base_cols = F.model_columns(f, False); reg_cols = base_cols + REG
        m0 = GBM(base_cols).fit(ftr, ytr); mr = GBM(reg_cols).fit(ftr, ytr)
        k = int(len(ftr) * 0.8); calf = ftr.iloc[k:]; calp = mr.cal_p; q67 = float(np.quantile(ftr["sigma_ratio"], 0.67)); hi = calf["sigma_ratio"].values >= q67
        thr0 = float(np.quantile(np.abs(calp - 0.5), 0.90)); thr1 = float(np.quantile(np.abs(calp[hi] - 0.5), 0.90)) if hi.sum() > 200 else 9.0
        r = pd.DataFrame({"date": fte["date"].values, "year": Y, "y": y_all[upto][tem], "ret": tg["ret_bps"].values[upto][tem], "p_base": m0.predict(fte), "p": mr.predict(fte), "sig": fte["sigma_next"].values, "sr": fte["sigma_ratio"].values,
                          "state": fte["hmm_state"].values, "thr0": thr0, "thr1": thr1, "q67": q67})
        res.append(r); log(f"{sym} {Y} done {round(time.time() - t0)}s  garch a={float(g.params['alpha[1]']):.3f} b={float(g.params['beta[1]']):.3f}  hmm state shares {np.round(np.bincount(fte['hmm_state'].astype(int), minlength=3) / len(fte), 2).tolist()}")
    return pd.concat(res, ignore_index=True)


def rule_masks(r):
    dev = np.abs(r.p.values - 0.5)
    E_pred = 2 * dev * r.sig.values * 0.7979
    return {"R0": dev > r.thr0.values, "R1": (r.sr.values >= r.q67.values) & (dev > r.thr1.values), "R2": E_pred >= 1.5}, E_pred


def cell(r):
    out = {"n": int(len(r)), "auc_regime": E.auc(r.y.values.astype(int), r.p.values), "auc_base": E.auc(r.y.values.astype(int), r.p_base.values), "rules": {}}
    masks, Ep = rule_masks(r)
    for name, m in masks.items():
        o = {"signals": int(m.sum())}
        if m.sum() >= 30:
            d = np.sign(r.p.values[m] - 0.5) * r.ret.values[m]; lo, hi = E.boot_mean(d - COST, r.date.values[m]); o.update(hit=float((d > 0).mean()), gross=float(d.mean()), net=float(d.mean() - COST), net_ci=(lo, hi), mean_sigma=float(r.sig.values[m].mean()))
            if name == "R2":
                o["mean_pred_E"] = float(Ep[m].mean()); allr = np.sign(r.p.values - 0.5) * r.ret.values; o["spearman_E_vs_realised"] = float(spearmanr(Ep, allr).statistic)
        out["rules"][name] = o
    # descriptive: R0 signals per HMM state
    m0 = masks["R0"]; st = {}
    for s in range(3):
        mm = m0 & (r.state.values == s)
        if mm.sum() >= 30: d = np.sign(r.p.values[mm] - 0.5) * r.ret.values[mm]; st[str(s)] = {"share_bars": float((r.state.values == s).mean()), "signals": int(mm.sum()), "gross": float(d.mean()), "sigma": float(r.sig.values[mm].mean())}
    out["by_hmm_state_R0"] = st; return out


def main():
    log = lambda m: print(m, flush=True); out = {"cells": {}}
    for sym in ("NIFTY", "BANKNIFTY"):
        r = study(sym, log)
        for name, yrs in (("P1", P1), ("P2", P2)):
            c = cell(r[r.year.isin(yrs)]); out["cells"][f"{sym}_{name}"] = c
            log(f"{sym} {name} AUC base {c['auc_base']:.4f} regime {c['auc_regime']:.4f} | " + " | ".join(f"{k}: n={v['signals']} gross={v.get('gross', float('nan')):+.2f} net={v.get('net', float('nan')):+.2f} ci={tuple(round(x, 2) for x in v['net_ci']) if 'net_ci' in v else None}" for k, v in c["rules"].items()))
            log(f"   R0 by HMM state: {c['by_hmm_state_R0']}")
    g = {}
    for rule in ("R1", "R2"):
        ok = True
        for cellname, c in out["cells"].items():
            v = c["rules"][rule]; g1 = v["signals"] >= 500 and "net_ci" in v and v["net"] > 0 and v["net_ci"][0] > 0; g2 = c["auc_regime"] >= c["auc_base"] - 0.002
            g3 = True if rule == "R1" else bool("mean_pred_E" in v and v["gross"] >= 0.5 * v["mean_pred_E"] and v["spearman_E_vs_realised"] > 0)
            ok &= bool(g1 and g2 and g3)
        g[rule] = ok
    out["gates"] = g; out["verdict"] = "REGIME LAYER SHADOW-WORTHY" if any(g.values()) else "REGIME LAYER NO-GO"; log("VERDICT " + out["verdict"] + " " + str(g))
    json.dump(out, open(os.path.join(HERE, "data", "research", "candle_ai", "results_regime.json"), "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
