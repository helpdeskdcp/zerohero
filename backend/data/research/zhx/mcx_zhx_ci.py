import os, sys, json
import numpy as np, pandas as pd
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "event_window.py")).read().split("for sym, dow in")[0])
rng = np.random.default_rng(5)
for sym in ("NATURALGAS", "CRUDEOIL"):
    d = load(sym); b = d.resample("5min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    cs = [{"time": int(t.timestamp() * 1000), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": 0.0} for t, r in b.iterrows()]
    r = runner.execute({"op": "run", "source": SRC, "candles": cs, "tf": "5", "symbol": sym}, count=False, timeout=290); P = {p["title"]: p["values"] for p in r["plots"]}
    rows = [(b.index[i].strftime("%Y%m%d"), float(v)) for i, v in enumerate(P["ZHX Realised R"]) if v is not None]; byday = {}
    for dday, v in rows: byday.setdefault(dday, []).append(v)
    ks = list(byday); vals = np.array([v for _, v in rows]); bs = [np.mean([x for k in rng.choice(ks, len(ks)) for x in byday[k]]) for _ in range(3000)]
    half = len(vals) // 2; print(sym, "n=%d mean R=%.3f 90%% day-cluster CI [%.3f, %.3f] | first half %.3f second half %.3f | win share %.2f | months:" % (len(vals), vals.mean(), np.percentile(bs, 5), np.percentile(bs, 95), vals[:half].mean(), vals[half:].mean(), (vals > 0).mean()),
          {m: (len(x), round(float(np.mean(x)), 2)) for m in sorted({k[:6] for k in ks}) for x in [[v for dd, v in rows if dd[:6] == m]]})
