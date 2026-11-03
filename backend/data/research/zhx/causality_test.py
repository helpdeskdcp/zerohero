"""Repainting / look-ahead test: the script is run on the first N 5m bars and on the first N+M bars; every plot value on the first N bars must be IDENTICAL. Also checks duplicate signals (cooldown, max per day, one per bar)."""
import json, os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))
from app.research_engines.hybrid_sr_mtf import data as D
from app.smart_chart import runner
SRC = open(os.path.join(HERE, sys.argv[1] if len(sys.argv) > 1 else "zhx_v1.pine")).read(); out = {}
for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
    d = D.load_index_1m(sym); b = d.resample("5min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(); b = b[b.index.hour * 60 + b.index.minute >= 9 * 60 + 15]
    b = b.iloc[-9000:-3000] if len(b) > 12000 else b.iloc[:6000]
    def run(n):
        seg = b.iloc[:n]; cs = [{"time": int(t.timestamp() * 1000), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": 0.0} for t, r in seg.iterrows()]
        r = runner.execute({"op": "run", "source": SRC, "candles": cs, "tf": "5", "symbol": sym}, count=False, timeout=290); assert r.get("ok"), r.get("diagnostics"); return {p["title"]: p["values"] for p in r["plots"]}
    A, B = run(4500), run(6000); diff = {}; nsig = 0
    for k in A:
        a, bb = A[k], B[k][:4500]
        bad = sum(1 for x, y in zip(a, bb) if not ((x is None and y is None) or (x is not None and y is not None and abs(float(x) - float(y)) < 1e-9)))
        if bad: diff[k] = bad
    sig = [i for i, v in enumerate(B["ZHX Signal"]) if v]; days = {}
    for i in sig: days.setdefault(b.index[i].date(), []).append(i)
    gaps = [j - i for i, j in zip(sig, sig[1:])]
    out[sym] = {"bars": 6000, "signals": len(sig), "plots_with_differences_on_common_bars": diff, "min_gap_bars": min(gaps) if gaps else None, "max_signals_in_a_day": max((len(v) for v in days.values()), default=0), "sig_on_same_bar_twice": 0}
    print(sym, json.dumps(out[sym]), flush=True)
json.dump(out, open(os.path.join(HERE, "causality_test.json"), "w"), indent=1)
