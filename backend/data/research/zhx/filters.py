"""ZHX filter study. PRE-REGISTERED before any result (3 variants, same gate as the earlier studies):
  F0 baseline (as validated)           F1 higher-timeframe agreement: 15m bias (C15>EMA20>EMA50 and EMA20 rising 3 bars -> +1, mirror -> -1) must NOT be against the trade side
  F2 session: entry 09:35-10:30 IST only                F3 F1 and F2 together
Gate (unchanged): on >= 2 of 3 indices OOS net/trade 90% day-cluster lower bound > 0, VALIDATION net/trade > 0, OOS trades >= 100. Nothing is tuned; times and the bias definition are the ones used in hybrid_sr_mtf.
Cost conversion = hybrid_sr_mtf.metrics.net_rs (same as validate.py). 4 variants x 3 indices = 12 looks: about 1 would clear zero by chance."""
import json, os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))
from app.research_engines.hybrid_sr_mtf import data as D, metrics as M
src = json.load(open(os.path.join(HERE, "validate_v1c.json")))
SPL = {"NIFTY": {"DEV": (20170601, 20211231), "VALIDATION": (20220101, 20231231), "OOS": (20240101, 20261031)}, "BANKNIFTY": {"DEV": (20210929, 20221231), "VALIDATION": (20230101, 20231231), "OOS": (20240101, 20261031)},
       "SENSEX": {"DEV": (20210929, 20221231), "VALIDATION": (20230101, 20231231), "OOS": (20240101, 20261031)}}
def bias15(sym):
    d = D.load_index_1m(sym); b = d.resample("15min", label="left", closed="left").agg({"close": "last"}).dropna()
    b = b[(b.index.hour * 60 + b.index.minute >= 555) & (b.index.hour * 60 + b.index.minute < 930)]
    e20 = b.close.ewm(span=20, adjust=False).mean(); e50 = b.close.ewm(span=50, adjust=False).mean()
    up = (b.close > e20) & (e20 > e50) & (e20 > e20.shift(3)); dn = (b.close < e20) & (e20 < e50) & (e20 < e20.shift(3))
    s = pd.Series(0, index=b.index); s[up] = 1; s[dn] = -1
    s.index = s.index + pd.Timedelta(minutes=15)                                                  # usable only when the 15m bar has COMPLETED
    return s
out = {}
for sym, r in src["results"].items():
    bs = bias15(sym); rows = r["trade_rows"]; out[sym] = {}
    for x in rows:
        t = pd.Timestamp(x["t_entry"], unit="s", tz="UTC").tz_convert(bs.index.tz) if bs.index.tz is not None else pd.Timestamp(x["t_entry"], unit="s", tz="UTC").tz_convert("Asia/Kolkata").tz_localize(None)
        x["_t"] = t; k = bs.index.searchsorted(t, side="right") - 1; x["_bias"] = int(bs.iloc[k]) if k >= 0 and (t - bs.index[k]) < pd.Timedelta(hours=20) else 0
        x["_min"] = t.hour * 60 + t.minute
    V = {"F0": lambda x: True, "F1": lambda x: x["_bias"] != -x["side"], "F2": lambda x: 575 <= x["_min"] <= 630, "F3": lambda x: x["_bias"] != -x["side"] and 575 <= x["_min"] <= 630}
    for name, f in V.items():
        out[sym][name] = {}
        for sp, (a, z) in SPL[sym].items():
            sel = [x for x in rows if a <= x["day"] <= z and f(x)]
            s = M.summarize([{"day": x["day"], "side": x["side"], "R": x["R"], "pts": x["pts"], "risk": x["risk"], "score": x["score"], "t_entry": int(x["_t"].timestamp()), "reason": "", "ambiguous": False, "mae": 0.0, "mfe": 0.0, "hit_t1": False, "hit_t2": False, "hit_t3": False} for x in sel], sym) if sel else {"n": 0}
            out[sym][name][sp] = {"n": s.get("n"), "gross_R": round(float(np.mean([x["R"] for x in sel])), 3) if sel else None, "net_per_trade": s.get("net_per_trade_rs"), "ci90": s.get("ci90_per_trade"), "pf": s.get("profit_factor")}
json.dump(out, open(os.path.join(HERE, "filters.json"), "w"), indent=1, default=str)
for sym in out:
    for name in out[sym]:
        o = out[sym][name]; print(sym, name, {sp: (v["n"], v["gross_R"], v["net_per_trade"], v["ci90"]) for sp, v in o.items()})
