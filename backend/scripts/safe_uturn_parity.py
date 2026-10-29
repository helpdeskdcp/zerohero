"""Run data/research/safe_ai/safe_uturn.pine in the Smart Chart's PineTS on real NIFTY 1-minute bars (Upstox research DB) and compare every bar's CALL/PUT idea with the Python rule app/arena/safe_ai/signals.detect.
usage: safe_uturn_parity.py [first_day] [n_days] [thrust]"""
import json, os, sqlite3, subprocess, sys
from datetime import datetime, timezone, timedelta
import numpy as np
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
from app.arena.safe_ai import signals as SG
PKG = os.path.join(HERE, "app", "smart_chart"); WORKER = os.path.join(PKG, "pine_runner", "worker.mjs"); DB = os.path.join(HERE, "data", "historical", "upstox", "upstox_research.db")
IST = timezone(timedelta(hours=5, minutes=30))
first = sys.argv[1] if len(sys.argv) > 1 else "2026-09-01"; ndays = int(sys.argv[2]) if len(sys.argv) > 2 else 20; th = float(sys.argv[3]) if len(sys.argv) > 3 else 0.005
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
days = [r[0] for r in c.execute("SELECT DISTINCT substr(timestamp,1,10) FROM normalized_bars WHERE symbol='NIFTY' AND timeframe='1m' AND substr(timestamp,1,10)>=? ORDER BY 1 LIMIT ?", (first, ndays))]
rows = c.execute("SELECT timestamp, open, high, low, close FROM normalized_bars WHERE symbol='NIFTY' AND timeframe='1m' AND substr(timestamp,1,10)>=? AND substr(timestamp,1,10)<=? ORDER BY timestamp", (days[0], days[-1])).fetchall()
ts = [datetime.fromisoformat(r[0]).timestamp() for r in rows]
candles = [dict(time=int(t * 1000), open=r[1], high=r[2], low=r[3], close=r[4], volume=0.0) for t, r in zip(ts, rows)]
src = open(os.path.join(HERE, "data", "research", "safe_ai", "safe_uturn.pine"), encoding="utf-8").read()
req = dict(op="run", source=src, candles=candles, tf="1", symbol="NIFTY", inputs={"Thrust % (v2 calibrated 0.60; 0.35 / 0.50 / 0.70 were pre-registered)": th * 100, "Only inside the trading window": False}, props={})
p = subprocess.run(["node", "--permission", f"--allow-fs-read={PKG}", "--max-old-space-size=1024", WORKER], input=json.dumps(req).encode(), capture_output=True, cwd=PKG, env={}, timeout=600)
out = json.loads(p.stdout.decode().strip().splitlines()[-1])
if not out.get("ok"): print("PINE RUN FAILED:", json.dumps(out.get("diagnostics"))[:1500]); sys.exit(1)
pl = {x["title"]: np.array([np.nan if v is None else v for v in x["values"]], float) for x in out["plots"]}; sig = pl["DBG_SIG"]
params = SG.Params(thrust=th, t_from=(0, 0), t_to=(23, 59), expiry_day_t_to=(23, 59))
mism = []; n_py = {"CE": 0, "PE": 0}; n_pine = {"CE": int((sig == 1).sum()), "PE": int((sig == -1).sum())}
by_day = {}
for t, r, k in zip(ts, rows, range(len(rows))):
    by_day.setdefault(r[0][:10], []).append((k, t + 60.0, r[4]))
for day, lst in by_day.items():
    ticks = [(t, px) for _, t, px in lst]
    for j, (k, t, px) in enumerate(lst):
        d = SG.detect(ticks[:j + 1], t, params); py = 1 if d and d["side"] == "CE" else -1 if d and d["side"] == "PE" else 0
        if py: n_py["CE" if py == 1 else "PE"] += 1
        if py != (0 if np.isnan(sig[k]) else int(sig[k])): mism.append((rows[k][0], py, sig[k]))
print(json.dumps({"days": len(days), "bars": len(rows), "pine": n_pine, "python": n_py, "mismatched_bars": len(mism), "first_mismatches": mism[:8]}, default=str))
