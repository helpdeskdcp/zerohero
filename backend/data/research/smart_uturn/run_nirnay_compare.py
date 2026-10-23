"""Nirnay 1 vs Nirnay 2 per SPEC_NIRNAY_COMPARE_2026-10-23.md."""
import json, os, sqlite3, sys
import numpy as np, pandas as pd
sys.path.insert(0, ".")
from app.smart_uturn import trend_pullback as TP
from app.smart_uturn.data import CandleStore, load_csv, load_dataframe, validate_one_minute
from app.smart_uturn.data.resampler import NSE
COST = 2.0; OUT = {}
V = {"PULLBACK (shipped)": TP.TPParams(mode="PULLBACK"), "BREAKOUT old caps": TP.TPParams(mode="BREAKOUT"),
     "NIRNAY1 trend-day": TP.TPParams(mode="BREAKOUT", max_risk_atr=10.0, max_loss_day=4, max_per_day=6), "NIRNAY2 volume-breakout": TP.TPParams(mode="VOLBO", max_risk_atr=10.0, max_loss_day=4, max_per_day=6)}
HTF = {"1": ("15", "60"), "5": ("15", "60"), "15": ("60", "240")}

def stats(t):
    if not len(t): return {"trades": 0}
    net = t.gross_R.to_numpy() - COST / t.risk.to_numpy(); g = t.gross_R.to_numpy(); rng = np.random.default_rng(7); boot = [net[rng.integers(0, len(net), len(net))].mean() for _ in range(2000)]
    days = max(1, t.time.dt.date.nunique())
    return {"trades": int(len(t)), "per_day": round(len(t) / days, 2), "win": round(float((g > 0).mean()), 3), "gross_R": round(float(g.mean()), 3), "net_R": round(float(net.mean()), 3), "net_ci95": [round(float(np.percentile(boot, 2.5)), 3), round(float(np.percentile(boot, 97.5)), 3)], "total_net_R": round(float(net.sum()), 1), "avg_risk_pts": round(float(t.risk.mean()), 1)}

def run(label, clean, charts, variants):
    store = CandleStore(clean, NSE)
    for tf in charts:
        for name in variants:
            t = TP.run(store.get(tf), store.get(HTF[tf][0]), store.get(HTF[tf][1]), V[name]); r = stats(t); OUT[f"{label}|{tf}|{name}"] = r
            print(f"{label:>14} chart {tf:>2} {name:<24}", r)

mode = sys.argv[1]
if mode == "futures":
    c = sqlite3.connect("file:data/market_history.db?mode=ro", uri=True, timeout=120)
    d = pd.read_sql("select session_date_ist d, instrument_key k, source, bar_start, o as open, h as high, l as low, c as close, v as volume from market_candles where symbol=? and kind='FUTURE' and tf='1m' and session_date_ist>='2026-07-01'", c, params=(sys.argv[2],))
    keep = []
    for day, g in d.groupby("d"):
        src = "DHAN_HISTORICAL" if (g.source == "DHAN_HISTORICAL").any() and day <= "2026-09-28" else "ANGELONE_CANDLES"
        g = g[g.source == src]; best = g.groupby("k").volume.sum().idxmax(); keep.append(g[g.k == best])
    d = pd.concat(keep).rename(columns={"bar_start": "datetime"}); d = d[["datetime", "open", "high", "low", "close", "volume"]]
    clean, q = validate_one_minute(load_dataframe(d), NSE); print("futures sessions", q["sessions"], "rows", q["rows_out"], "missing min", q["missing_minutes_total"], "volume zero share", q.get("volume_zero_share"))
    run("NIFTYFUT " + sys.argv[2], clean, ["1", "5"], list(V))
else:
    KG = "data/historical/kaggle/mavrick136__10-years-of-nifty-50-1-minute-data-20152025/NIFTY_1MIN_2015-2025.csv"; raw = load_csv(KG)
    for per, (a, b) in {"P1": ("2015-01-01", "2019-12-31"), "P2": ("2020-01-01", "2025-12-31")}.items():
        x = raw[(raw.index >= pd.Timestamp(a, tz="Asia/Kolkata")) & (raw.index < pd.Timestamp(b, tz="Asia/Kolkata") + pd.Timedelta(days=1))]
        clean, _ = validate_one_minute(x, NSE); run("INDEX " + per, clean, ["5", "15"], ["PULLBACK (shipped)", "BREAKOUT old caps", "NIRNAY1 trend-day"])
json.dump(OUT, open(f"data/research/smart_uturn/results_nirnay_{mode}{'_' + sys.argv[2] if mode == 'futures' else ''}.json", "w"), indent=1)
