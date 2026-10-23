"""Mirror replay per SPEC_TREND_PULLBACK_2026-10-23.md. usage: python data/research/smart_uturn/run_trend_pullback.py <one_minute_csv> <label> [P1|P2|ALL]"""
import json, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, ".")
from app.smart_uturn import trend_pullback as TP
from app.smart_uturn.data import CandleStore, load_csv, validate_one_minute
from app.smart_uturn.data.resampler import NSE
path, label = sys.argv[1], sys.argv[2]; per = sys.argv[3] if len(sys.argv) > 3 else "ALL"
PER = {"P1": ("2015-01-01", "2019-12-31"), "P2": ("2020-01-01", "2025-12-31"), "ALL": ("1900-01-01", "2100-01-01")}
raw = load_csv(path); a, b = (pd.Timestamp(x, tz="Asia/Kolkata") for x in PER[per]); raw = raw[(raw.index >= a) & (raw.index < b + pd.Timedelta(days=1))]
clean, q = validate_one_minute(raw, NSE); store = CandleStore(clean, NSE); res = {"label": label, "period": per}; COST = 2.0
MODES = (sys.argv[4].split(",") if len(sys.argv) > 4 else ["PULLBACK"])   # e.g. PULLBACK:ORIGINAL,PULLBACK:HUNT,PULLBACK:HUNT_FRAC,BREAKOUT:HUNT
for mode, (tf, (ta_, tb_)) in [(m, x) for m in MODES for x in (("5", ("15", "60")), ("15", ("60", "240")))]:
    t = TP.run(store.get(tf), store.get(ta_), store.get(tb_), TP.TPParams(mode=mode.split(":")[0], entry_style=("HUNT" if ":HUNT" in mode else "ORIGINAL"), hunt_stop=("FRAC" if "FRAC" in mode else "SWEEP"))); r = {"trades": int(len(t))}
    if len(t):
        net = t.gross_R.to_numpy() - COST / t.risk.to_numpy(); g = t.gross_R.to_numpy(); rng = np.random.default_rng(7)
        boot = [net[rng.integers(0, len(net), len(net))].mean() for _ in range(2000)]
        r.update(win_rate=float((g > 0).mean()), gross_R_mean=float(g.mean()), net_R_mean=float(net.mean()), net_R_ci95=[float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))], sum_gross_R=float(g.sum()),
                 t1=float(t.h1.mean()), t2=float(t.h2.mean()), t3=float(t.h3.mean()), stop_share=float((t.end == "STOP").mean()), avg_risk_pts=float(t.risk.mean()), trades_per_day=float(len(t) / max(1, t.time.dt.date.nunique())))
    res[mode + "_" + tf] = r; print(label, per, mode, "chart", tf, {k: (round(v, 3) if isinstance(v, float) else ([round(x, 3) for x in v] if isinstance(v, list) else v)) for k, v in r.items()})
json.dump(res, open("data/research/smart_uturn/results_trend_pullback_%s_%s_%s.json" % (label, per, "-".join(MODES).replace(":", "_")), "w"), indent=1)
