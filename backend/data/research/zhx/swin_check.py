import json, os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))
exec(open(os.path.join(HERE, "ablation.py")).read().split("base = pooled_net")[0])          # reuse loaders / helpers (no outputs)
from app.research_engines.hybrid_sr_mtf import data as D
rng = np.random.default_rng(7)
def net_list(sym, rows):                                                                        # per-trade net Rs via the same conversion: trade value = summ-level mean is not per trade, so convert each trade
    out = []
    for x in rows:
        s = M.summarize([{"day": x["day"], "side": x["side"], "R": x["R"], "pts": x["pts"], "risk": x["risk"], "score": x["score"], "t_entry": x["t_entry"], "reason": "", "ambiguous": False, "mae": 0.0, "mfe": 0.0, "hit_t1": False, "hit_t2": False, "hit_t3": False}], sym)
        out.append((x["day"], s.get("net_per_trade_rs")))
    return out
pool = []
for sym in rows_all:
    sel = split(rows_all[sym], sym, "OOS", lambda x: ok(x, "S_win")); pool += [(sym, d, v) for d, v in net_list(sym, sel)]
vals = np.array([v for _, _, v in pool], float); days = sorted({(s, d) for s, d, _ in pool}); idx = {k: i for i, k in enumerate(days)}
by = [[] for _ in days]
for s, d, v in pool: by[idx[(s, d)]].append(v)
bs = []
for _ in range(4000):
    pick = rng.integers(0, len(days), len(days)); xs = [v for i in pick for v in by[i]]; bs.append(np.mean(xs))
print("S_win OOS pooled n=%d mean=%.1f ci90=[%.1f, %.1f]  share positive=%.2f" % (len(vals), vals.mean(), np.percentile(bs, 5), np.percentile(bs, 95), (vals > 0).mean()))
for sym in rows_all:
    ys = {}
    for y in range(2017, 2027):
        sel = [x for x in rows_all[sym] if x["day"] // 10000 == y and ok(x, "S_win")]
        if len(sel) >= 8: s = summ(sel, sym); ys[y] = (s["n"], s["gross_R"], s["net"])
    print(sym, "S_win by year (n, grossR, net):", ys)
# MFE / MAE (in R) of the baseline trades, from 5m bars between entry and exit (needs entry_px / exit_ts)
for sym, r in src["results"].items():
    d = D.load_index_1m(sym); b = d.resample("5min", label="left", closed="left").agg({"high": "max", "low": "min"}).dropna(); ti = b.index.view("int64") // 10**9 if hasattr(b.index, "view") else None
    ts = np.array([t.timestamp() for t in b.index]); H = b.high.values; L = b.low.values; mfe, mae, ttm = [], [], []
    for x in r["trade_rows"]:
        if "entry_px" not in x or not x["risk"]: continue
        i0 = np.searchsorted(ts, x["t_entry"]); i1 = np.searchsorted(ts, x["exit_ts"], side="right")
        if i1 <= i0: continue
        hi, lo = H[i0:i1].max(), L[i0:i1].min(); e = x["entry_px"]
        f, a = ((hi - e), (e - lo)) if x["side"] > 0 else ((e - lo), (hi - e)); mfe.append(f / x["risk"]); mae.append(a / x["risk"]); ttm.append((x["exit_ts"] - x["t_entry"]) / 60.0)
    print(sym, "baseline MFE mean %.2fR median %.2fR | MAE mean %.2fR median %.2fR | median minutes in trade %.0f (n=%d)" % (np.mean(mfe), np.median(mfe), np.mean(mae), np.median(mae), np.median(ttm), len(mfe)))
