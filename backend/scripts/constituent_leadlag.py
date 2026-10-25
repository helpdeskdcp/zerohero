"""Pre-registered: data/research/constituent_leadlag/SPEC_2026-10-25.md"""
import json, os, sqlite3, sys, warnings
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE); warnings.filterwarnings("ignore")
DB = os.path.join(HERE, "data", "historical", "stocks_1m", "stocks_1m.db"); OUT = os.path.join(HERE, "data", "research", "constituent_leadlag")
HEAVY = {"HDFCBANK": 13, "RELIANCE": 9, "ICICIBANK": 8, "INFY": 5, "BHARTIARTL": 4.5, "ITC": 4, "LT": 4, "TCS": 3.5, "AXISBANK": 3, "SBIN": 3}
IST = 5.5 * 3600; SPLIT = pd.Timestamp("2025-10-01").date(); COST_BP = 1.5; LAGS = 5


def sessions():
    c = sqlite3.connect(DB); df = pd.read_sql("SELECT sym, ts, o, c FROM bars", c); c.close()
    df["t"] = pd.to_datetime(df["ts"] + IST, unit="s"); df["day"] = df["t"].dt.date; df["m"] = df["t"].dt.hour * 60 + df["t"].dt.minute - 555          # minute of session 0..374
    df = df[(df["m"] >= 0) & (df["m"] < 375)]; C = df.pivot_table(index=["day", "m"], columns="sym", values="c"); O = df[df.sym == "NIFTY"].set_index(["day", "m"])["o"]
    return C, O


def build(C, O):
    syms = [s for s in C.columns if s != "NIFTY"]; hv = [s for s in HEAVY if s in syms]; rest = [s for s in syms if s not in HEAVY]; w = np.array([HEAVY[s] for s in hv], float); w /= w.sum()
    days, X, Y, meta, dropped = [], {}, {}, [], 0
    for d, g in C.groupby(level=0):
        g = g.droplevel(0).reindex(range(375)); idx = g["NIFTY"]
        if idx.isna().sum() > 5: dropped += 1; continue
        g = g.ffill().bfill(); lr = np.log(g).diff(); lr.iloc[0] = 0.0
        if (lr[syms].abs() > 0.05).any().any(): dropped += 1; continue
        H = (lr[hv].values * w).sum(1); R = lr[rest].mean(1).values; I = lr["NIFTY"].values; o = O.loc[d].reindex(range(375)).ffill().bfill().values; days.append(d); X[d] = (H, R, I); Y[d] = (g["NIFTY"].values, o)
    return days, X, Y, dropped, hv, rest


def design(days, X, Y, h, which=("H", "R", "I")):
    rows, tgt, rec = [], [], []
    for d in days:
        H, R, I = X[d]; cl, op = Y[d]; ser = {"H": H, "R": R, "I": I}
        for t in range(LAGS, 375 - h - 1):
            f = [ser[k][t - j] for k in which for j in range(LAGS)]
            rows.append(f); tgt.append(np.log(cl[t + h] / cl[t])); rec.append((d, t, np.log(cl[t + h + 1 - 1] / op[t + 1]) if t + 1 < 375 else np.nan))
    return np.array(rows), np.array(tgt), rec


def ridge(A, y, alpha=1.0):
    mu, sd = A.mean(0), A.std(0) + 1e-12; Z = (A - mu) / sd; ym = y.mean(); w = np.linalg.solve(Z.T @ Z + alpha * np.eye(Z.shape[1]), Z.T @ (y - ym)); return lambda B: ((B - mu) / sd) @ w + ym, w


def r2(y, p): return 1 - ((y - p) ** 2).sum() / ((y - y.mean()) ** 2).sum()


def run(h, days, X, Y, rng):
    tr = [d for d in days if d < SPLIT]; te = [d for d in days if d >= SPLIT]; out = {}
    A, y, _ = design(tr, X, Y, h); B, yt, rec = design(te, X, Y, h)
    f_full, w = ridge(A, y); pf = f_full(B)
    cols = lambda keys: [i for i, k in enumerate("HRI") for _ in range(LAGS) if k in keys]
    res = {}
    for name, keys in (("full", "HRI"), ("own_only", "I"), ("heavy+own", "HI"), ("rest+own", "RI")):
        ci = [i for i in range(3 * LAGS) if "HRI"[i // LAGS] in keys]; f, _ = ridge(A[:, ci], y); res[name] = float(r2(yt, f(B[:, ci])))
    out["r2"] = res; out["r2_improvement_full_vs_own"] = res["full"] - res["own_only"]
    # permutation: replace the HEAVY/REST lag blocks of each test session by another session's blocks
    nper = 2000; days_te = te; blocks = {d: B[[i for i, r in enumerate(rec) if r[0] == d]] for d in days_te}; own_ci = list(range(2 * LAGS, 3 * LAGS)); hr_ci = list(range(0, 2 * LAGS)); null = []
    for _ in range(nper if h == 1 else 400):
        pm = rng.permutation(len(days_te)); Bp = np.concatenate([np.concatenate([blocks[days_te[pm[k]]][:, hr_ci], blocks[d][:, own_ci]], axis=1) if len(blocks[days_te[pm[k]]]) == len(blocks[d]) else blocks[d][:, hr_ci + own_ci] for k, d in enumerate(days_te)]); null.append(r2(yt, f_full(Bp)))
    # note: B column order is H(0-4), R(5-9), I(10-14)
    out["p_perm"] = float((np.array(null) >= res["full"]).mean()); out["perm_n"] = len(null)
    # trading rule on top 10% |pred| (threshold from training predictions)
    thr = np.percentile(abs(f_full(A)), 90); trades = []; last = {}
    for p, r in zip(pf, rec):
        d, t, _ = r
        if abs(p) < thr or t < last.get(d, -1) or t + 1 + h > 374: continue
        cl, op = Y[d]; ret = np.log(cl[t + h] / op[t + 1]) * np.sign(p) * 1e4 - COST_BP; trades.append((d, ret)); last[d] = t + 1 + h
    tdf = pd.DataFrame(trades, columns=["day", "net_bp"])
    if len(tdf) > 30:
        g = tdf.groupby("day")["net_bp"]; s, n = g.sum().values, g.count().values; bb = [s[k].sum() / n[k].sum() for k in (rng.integers(0, len(s), len(s)) for _ in range(3000))]; lo = float(np.percentile(bb, 1.25))
        ud = sorted(tdf["day"].unique()); half = ud[len(ud) // 2]; h1, h2 = tdf[tdf.day <= half]["net_bp"].mean(), tdf[tdf.day > half]["net_bp"].mean()
        out["trade"] = dict(n=int(len(tdf)), mean_net_bp=float(tdf.net_bp.mean()), ci_lo=lo, half1=float(h1), half2=float(h2), gross_bp=float(tdf.net_bp.mean() + COST_BP))
    else: out["trade"] = dict(n=int(len(tdf)))
    t = out["trade"]; out["verdict"] = "GO-SHADOW" if out["r2_improvement_full_vs_own"] > 0 and out["p_perm"] < 0.025 and t.get("n", 0) >= 300 and t.get("mean_net_bp", -1) > 0 and t.get("ci_lo", -1) > 0 and t.get("half1", -1) > 0 and t.get("half2", -1) > 0 else "NO-GO"
    out["coef_abs_sum"] = {k: float(np.abs(w[i * LAGS:(i + 1) * LAGS]).sum()) for i, k in enumerate("HRI")}; return out


if __name__ == "__main__":
    C, O = sessions(); days, X, Y, dropped, hv, rest = build(C, O); print("sessions", len(days), "dropped", dropped, "heavy", len(hv), "rest", len(rest), flush=True); rng = np.random.default_rng(5); res = {"sessions": len(days), "dropped": dropped}
    for h in (1, 5):
        res[f"h{h}"] = run(h, days, X, Y, rng); print(f"h{h}", json.dumps(res[f"h{h}"]), flush=True)
    json.dump(res, open(os.path.join(OUT, "results.json"), "w"), indent=1)
