"""DESCRIPTIVE only (not a pre-registered gate): the Pine signals on NG futures bars from the capture (2026-06-30 .. 2026-10-23), MCX session (no signal after 22:45, flat 23:15), trailing-stop exit (X2),
entry next bar open. Cost is an ASSUMPTION (3 bp / 6 bp round trip). Capture candles are late and ~8 % partial, so this is indicative at best. Output: data/research/winners_scalper/ng_descriptive.json"""
import json, os, sys, time
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
from app.winners_scalper import live, backtest as WB
from app.zone_breakout import backtest as ZBB
b = live.load_bars("NATURALGAS", time.time(), days=130)
out = {}
for var in ("X1", "X2"):
    t = WB.simulate(b, var, end_min=23 * 60 + 15, last_sig_min=22 * 60 + 45)
    for cost in (3.0, 6.0):
        t["net_R"] = t["R"] - cost / t["risk_bp"]; t["session"] = t["session"].astype(str); sm = ZBB.summary(t, 0.05)
        out[f"{var}_cost{cost:g}bp"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in sm.items() if k in ("n", "win_rate", "mean_R_gross", "mean_R_net", "ci_lo", "median_risk_bp", "tp", "sl", "eod")}
        if cost == 3.0 and var == "X2": out["by_month_X2_3bp"] = {k: round(float(v), 3) for k, v in t.groupby(pd.to_datetime(t.entry_ts).dt.strftime("%Y-%m"))["net_R"].mean().items()}; out["n_by_month"] = t.groupby(pd.to_datetime(t.entry_ts).dt.strftime("%Y-%m")).size().to_dict()
out["bars"] = len(b); out["span"] = [str(b.timestamp.iloc[0]), str(b.timestamp.iloc[-1])]
json.dump(out, open(os.path.join(HERE, "data", "research", "winners_scalper", "ng_descriptive.json"), "w"), indent=1, default=str); print(json.dumps(out, indent=1, default=str))
