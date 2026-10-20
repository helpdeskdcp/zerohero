"""[EXPORT VARIANT of pa_lab_backtest.py — identical fill/exit logic, additionally writes the trade list] Price-action engine (app/pa_of_lab) on NIFTY 5m index bars, futures-style, net of 6.3 bps. Fill rule: signal at bar i close; entry only if bar i+1
trades through the trigger (filled at max/min(open, trigger)); stop-first on same-bar touch; flat at 15:10 bar close; one position at a time."""
import sys, os, math, datetime as dt
import numpy as np
from scipy import stats
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.research_engines.smart_bos.data import load_1m
from app.research_engines.hybrid_mp.engine import resample
from app.pa_of_lab.price_action import signal_at

COST = float(sys.argv[1]) if len(sys.argv) > 1 else 6.3
START = sys.argv[2] if len(sys.argv) > 2 else "2021-01-01"
b1 = load_1m("NIFTY", "INDEX", start=START, end="2027-01-01", prefer="ANGELONE_CANDLES")
ch = resample(b1, 5, 555, 930)
IST = dt.timedelta(hours=5, minutes=30)
ts = [dt.datetime.utcfromtimestamp(int(t)) + IST for t in ch["t"]]
bars = [dict(t=int(ch["t"][i]), o=float(ch["o"][i]), h=float(ch["h"][i]), l=float(ch["l"][i]), c=float(ch["c"][i]), day=ts[i].strftime("%Y-%m-%d")) for i in range(len(ts))]
mins = [t.hour * 60 + t.minute for t in ts]
net, days, free, rows = [], [], 0, []
for i in range(len(bars) - 2):
    if i < free or mins[i] < 9 * 60 + 45 or mins[i] > 14 * 60 + 30: continue
    s = signal_at(bars, i)
    if not s: continue
    j = i + 1
    if bars[j]["day"] != bars[i]["day"]: continue
    b = bars[j]
    if s.side > 0 and b["h"] >= s.entry: fill = max(b["o"], s.entry)
    elif s.side < 0 and b["l"] <= s.entry: fill = min(b["o"], s.entry)
    else: continue
    px, k = None, j
    while k < len(bars) and bars[k]["day"] == bars[i]["day"] and mins[k] <= 15 * 60 + 10:
        x = bars[k]
        hit_stop = x["l"] <= s.stop if s.side > 0 else x["h"] >= s.stop
        hit_tgt = x["h"] >= s.target if s.side > 0 else x["l"] <= s.target
        if hit_stop: px = s.stop; break
        if hit_tgt: px = s.target; break
        k += 1
    if px is None: k = min(k, len(bars) - 1); px = bars[k - 1]["c"] if k > j else fill
    g = s.side * (px - fill) / fill * 1e4
    net.append(g - COST); days.append(bars[i]["day"]); free = k + 1
    rows.append((bars[i]["day"], s.side, round(fill, 2), round(px, 2), round(g, 3), round(g - COST, 3)))
a = np.array(net)
if len(a) < 3: print("n", len(a)); sys.exit()
se = a.std(ddof=1) / math.sqrt(len(a)); t = a.mean() / se
print(f"cost {COST} bps | n={len(a)} days={len(set(days))} | gross {a.mean()+COST:+.2f} net {a.mean():+.2f} bps/trade | SE {se:.2f} t {t:.2f} p1 {1-stats.t.cdf(t,len(a)-1):.3f} | win {np.mean(a>0):.1%} | sum {a.sum():+.0f} bps")
yrs = {}
for d, v in zip(days, net): yrs.setdefault(d[:4], []).append(v)
print({y: (len(v), round(float(np.mean(v)), 2)) for y, v in sorted(yrs.items())})

import csv
os.makedirs("data/research/pa_of_lab", exist_ok=True)
with open("data/research/pa_of_lab/pa_baseline_trades.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["date", "side", "entry", "exit", "gross_bps", "net_bps"]); w.writerows(rows)
print("wrote", len(rows), "trades")
