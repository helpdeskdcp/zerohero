"""Pre-registered: data/research/winners_scalper/SPEC_OPTIONS_2026-10-25.md"""
import json, os, sqlite3, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
from app.winners_scalper import backtest as WB
from app.zone_breakout import data as DT
from app.institutional_edge.costs import estimate_index_option_cost
CFG = {"NIFTY": dict(db="option_flow.db", step=50, lot=65, slip=0.5), "SENSEX": dict(db="option_flow_sensex.db", step=100, lot=20, slip=2.0)}; ALPHA = 0.05 / 4
OUT = os.path.join(HERE, "data", "research", "winners_scalper")


def px(c, expiry, strike, ot, boundary_epoch, back=5):
    for m in range(1, back + 1):
        r = c.execute("SELECT c FROM bars WHERE expiry=? AND strike=? AND otype=? AND ts=?", (expiry, strike, ot, boundary_epoch - 60 * m)).fetchone()
        if r and r[0] is not None and r[0] > 0: return r[0]
    return None


def trades_for(sym):
    cfg = CFG[sym]; c = sqlite3.connect(os.path.join(HERE, "data", "historical", "option_flow", cfg["db"])); exps = [(e, set(d.split(","))) for e, d in c.execute("SELECT expiry, days FROM done ORDER BY expiry")]
    d5 = DT.load_5m(sym); d15 = DT.to_15m(d5); t = WB.run(sym, 15, "X2", bars=d5); t = t[pd.DatetimeIndex(t.entry_ts).tz_convert("Asia/Kolkata") >= pd.Timestamp("2024-10-03", tz="Asia/Kolkata")].reset_index(drop=True)
    opn = dict(zip(pd.DatetimeIndex(d15.timestamp), d15.open)); cls = dict(zip(pd.DatetimeIndex(d15.timestamp), d15.close)); rows = []; skip = {}
    for _, r in t.iterrows():
        D = pd.Timestamp(r.entry_ts).tz_convert("Asia/Kolkata").strftime("%Y-%m-%d"); ex = next((e for e, ds in exps if D in ds and e >= D), None)
        if ex is None: skip["no_expiry"] = skip.get("no_expiry", 0) + 1; continue
        atm = round(r.open_j / cfg["step"]) * cfg["step"]; ot = "CE" if r.side == "BUY" else "PE"
        te, tx = int(pd.Timestamp(r.entry_ts).timestamp()), int(pd.Timestamp(r.exit_ts).timestamp()); pe, pxx = px(c, ex, atm, ot, te), px(c, ex, atm, ot, tx)
        if pe is None or pxx is None or tx <= te: skip["no_price"] = skip.get("no_price", 0) + 1; continue
        s = cfg["slip"]; fe, fx = pe + s, max(0.05, pxx - s); cost = estimate_index_option_cost(fe, fx, cfg["lot"], lots=1, slippage_points=0.0); cost = cost.get("total_cost", cost.get("total", 0.0))
        gross = (fx - fe) * cfg["lot"]; bx = pd.Timestamp(r.exit_ts); idx_x = opn.get(bx, cls.get(bx - pd.Timedelta(minutes=15), np.nan)); sgn = 1 if r.side == "BUY" else -1
        rows.append(dict(sym=sym, entry_ts=str(r.entry_ts), day=D, year=int(D[:4]), side=r.side, expiry=ex, dte0=(ex == D), strike=atm, prem_in=pe, prem_out=pxx, reason=r.reason, gross=gross, cost=cost, net=gross - cost, prem_ret=(fx - fe) / fe,
                         idx_ctrl_bp=sgn * (idx_x - r.open_j) / r.open_j * 1e4 - DT.COST_BP[sym]))
    return pd.DataFrame(rows), skip, len(t)


def cell(df, nmin=80, seed=9):
    n = len(df)
    if n == 0: return dict(n=0, verdict="INSUFFICIENT_SAMPLE")
    x = df["net"].values; days = df["day"].values; ud = sorted(set(days)); g = {d: x[days == d] for d in ud}; rng = np.random.default_rng(seed); bm = []
    for _ in range(4000): bm.append(np.concatenate([g[ud[i]] for i in rng.integers(0, len(ud), len(ud))]).mean())
    lo, hi = np.percentile(bm, [100 * ALPHA / 2, 100 * (1 - ALPHA / 2)]); df = df.sort_values("day"); h = n // 2; h1, h2 = df["net"].iloc[:h].mean(), df["net"].iloc[h:].mean(); srt = np.sort(x)
    ok = n >= nmin and x.mean() > 0 and lo > 0 and h1 > 0 and h2 > 0
    return dict(n=int(n), mean_net=float(x.mean()), mean_gross=float(df["gross"].mean()), mean_cost=float(df["cost"].mean()), ci=[float(lo), float(hi)], half1=float(h1), half2=float(h2), win=float((x > 0).mean()), worst=float(srt[0]),
                prem_ret_mean=float(df["prem_ret"].mean()), idx_ctrl_bp_mean=float(np.nanmean(df["idx_ctrl_bp"])), median_prem_in=float(df["prem_in"].median()), by_year={int(k): round(float(v), 1) for k, v in df.groupby("year")["net"].mean().items()},
                verdict="GO-SHADOW" if ok else ("INSUFFICIENT_SAMPLE" if n < nmin else "NO-GO"))


if __name__ == "__main__":
    res = {}
    for sym in CFG:
        df, skip, nsig = trades_for(sym); print(sym, "signals", nsig, "used", len(df), "skipped", skip, flush=True); df.to_csv(os.path.join(OUT, f"options_trades_{sym}.csv"), index=False)
        for name, d in (("dte>=1", df[~df.dte0]), ("all", df)):
            res[f"{sym}_{name}"] = cell(d); r = res[f"{sym}_{name}"]; print(sym, name, json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if k not in ("by_year", "ci")}), "ci", [round(v, 1) for v in r.get("ci", [])], "years", r.get("by_year"), flush=True)
    json.dump(res, open(os.path.join(OUT, "results_options.json"), "w"), indent=1)
