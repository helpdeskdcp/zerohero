"""Run the owner's Pine script inside the Smart Chart's Pine interpreter (PineTS) on real NIFTY 15m bars and compare its signals with the Python port (app/winners_scalper).
usage: pine_parity_check.py <pine file> [n_bars]"""
import json, os, subprocess, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
from app.zone_breakout import data as DT
from app.winners_scalper import strategy as WS
PKG = os.path.join(HERE, "app", "smart_chart"); WORKER = os.path.join(PKG, "pine_runner", "worker.mjs")
DBG = """
plot(initialBuy or lateStrongBuy ? 1 : 0, 'DBG_BUY')
plot(initialSell or lateStrongSell ? 1 : 0, 'DBG_SELL')
plot(dir, 'DBG_DIR')
plot(longStop, 'DBG_LS')
plot(shortStop, 'DBG_SS')
plot(atr, 'DBG_ATR')
plot(adx, 'DBG_ADX')
"""
if __name__ == "__main__":
    src = open(sys.argv[1], encoding="utf-8").read() + DBG; n = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
    d = DT.to_15m(DT.load_5m("NIFTY")).tail(n).reset_index(drop=True); ts = pd.DatetimeIndex(d["timestamp"])
    candles = [dict(time=int(t.timestamp() * 1000), open=float(o), high=float(h), low=float(l), close=float(c), volume=0.0) for t, o, h, l, c in zip(ts, d.open, d.high, d.low, d.close)]
    req = dict(op="run", source=src, candles=candles, tf="15", symbol="NIFTY", inputs={}, props={})
    p = subprocess.run(["node", "--permission", f"--allow-fs-read={PKG}", "--max-old-space-size=1024", WORKER], input=json.dumps(req).encode(), capture_output=True, cwd=PKG, env={}, timeout=600)
    out = json.loads(p.stdout.decode().strip().splitlines()[-1])
    if not out.get("ok"): print("PINE RUN FAILED:", json.dumps(out.get("diagnostics"))[:1500]); sys.exit(1)
    al = out.get("alerts") or []; print("alerts fired:", len(al), "| sample:", json.dumps(al[-1])[:400] if al else None)
    print("warnings:", out.get("warnings")[:3]); pl = {x["title"]: np.array([np.nan if v is None else v for v in x["values"]], float) for x in out["plots"]}
    py = WS.signals(d); cmp = {}
    for name, col in (("DBG_BUY", "buy"), ("DBG_SELL", "sell")):
        a = pl[name] > 0; b = py[col].values; cmp[name] = dict(pine=int(a.sum()), python=int(b.sum()), mismatch=int((a != b).sum()))
    for name, col in (("DBG_DIR", "dir"), ("DBG_LS", "long_stop"), ("DBG_SS", "short_stop"), ("DBG_ATR", "atr"), ("DBG_ADX", "adx")):
        a, b = pl[name], py[col].values.astype(float); m = ~np.isnan(a) & ~np.isnan(b); cmp[name] = dict(compared=int(m.sum()), max_abs_diff=float(np.max(np.abs(a[m] - b[m]))) if m.any() else None, nan_mismatch=int((np.isnan(a) != np.isnan(b)).sum()))
    print(json.dumps(cmp, indent=1)); idx = np.where((pl["DBG_BUY"] > 0) != py["buy"].values)[0]; print("first buy mismatches (bar idx):", idx[:10].tolist())
    if "--detail" in sys.argv:
        for i in range(26, 40): print(i, "dir", int(pl["DBG_DIR"][i]), "pine_buy", int(pl["DBG_BUY"][i]), "py_buy", int(py["buy"].iat[i]), "py_init", int(py["initial_buy"].iat[i]), "py_late", int(py["late_buy"].iat[i]), "adx", round(py["adx"].iat[i], 2), "ma", round(py["ma"].iat[i], 1) if not np.isnan(py["ma"].iat[i]) else None)
