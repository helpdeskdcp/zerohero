import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))
from app.research_engines.hybrid_sr_mtf import data as D
from app.smart_chart import runner
V1 = open(os.path.join(HERE, "zhx_v1.pine")).read(); DEV = open(os.path.join(HERE, "..", "zerohero_edge", "zhx_dev_v2.pine")).read(); res = {}
for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
    d = D.load_index_1m(sym); b = d.resample("5min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(); b = b[b.index.hour * 60 + b.index.minute >= 9 * 60 + 15].iloc[-6000:]
    cs = [{"time": int(t.timestamp() * 1000), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": 0.0} for t, r in b.iterrows()]
    def run(src): r = runner.execute({"op": "run", "source": src, "candles": cs, "tf": "5", "symbol": sym}, count=False, timeout=290); assert r.get("ok"), r.get("diagnostics"); return {p["title"]: p["values"] for p in r["plots"]}, r
    A, ra = run(V1); B, rb = run(DEV)
    same = all(A[k] == B[k] for k in A); n1 = sum(1 for v in A["ZHX Signal"] if v); res[sym] = {"identical_plots_with_defaults": same, "signals_v1": n1, "compile_warnings_dev": [x for x in (rb.get("diagnostics") or []) if x.get("severity") not in ("info",)][:3]}
    print(sym, res[sym], flush=True)
json.dump(res, open(os.path.join(HERE, "regress.json"), "w"), indent=1)
