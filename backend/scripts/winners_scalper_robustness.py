import sys, numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, "/root/zerohero/backend")
from app.zone_breakout import backtest as ZBB, data as DT
from app.winners_scalper import backtest as WB, strategy as WS
bars = {s: DT.load_5m(s) for s in DT.INDICES}
t = pd.concat([WB.run(s, 15, "X2", bars=bars[s]) for s in DT.INDICES], ignore_index=True)
f = lambda x: dict(n=len(x), win=round((x.R > 0).mean(), 3), gross=round(x.R.mean(), 3), net=round(x.net_R.mean(), 3), bp_net=round((x.net_R * x.risk_bp).mean(), 2))
print("ALL", f(t)); print("BY SIDE"); [print(k, f(g)) for k, g in t.groupby("side")]
print("BY INST"); [print(k, f(g)) for k, g in t.groupby("inst")]
print("BY YEAR"); [print(k, f(g)) for k, g in t.groupby("year")]
print("BY EXIT"); [print(k, f(g)) for k, g in t.groupby("reason")]
t2 = t.copy(); t2["net_R"] = t2["R"] - 2 * DT.COST_BP[t2.inst.iloc[0]] / t2["risk_bp"]
c2 = t.R - t.inst.map(DT.COST_BP) * 3 / t.risk_bp; print("3x cost net", round(c2.mean(), 3))
# placebo: same entry bars, opposite direction using mirrored stops (flip the sign of the price series)
def mirror(b): m = b.copy(); m["open"], m["high"], m["low"], m["close"] = -b["open"], -b["low"], -b["high"], -b["close"]; return m
pl = []
for s in DT.INDICES:
    d = DT.to_15m(bars[s]); m = mirror(d); m[["open", "high", "low", "close"]] += 100000   # keep positive for bp calc; mirror = inverted market
    x = WB.simulate(m, "X2"); 
    if len(x): x["inst"] = s; x["net_R"] = x["R"] - DT.COST_BP[s] / x["risk_bp"]; pl.append(x)
pl = pd.concat(pl); print("MIRROR (inverted market, same rules)", f(pl))
# entry delayed one more bar: shift signal by 1 bar -> emulate by dropping first bar of each trade is complex; use 30m and 1h for timeframe monotonicity
def tfres(d5, m):
    x = d5.set_index("timestamp").resample(f"{m}min", offset="15min", origin="start_day").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index(); return x
for m in (30, 60):
    rr = []
    for s in DT.INDICES:
        d = tfres(bars[s], m); x = WB.simulate(d, "X2")
        if len(x): x["inst"] = s; x["net_R"] = x["R"] - DT.COST_BP[s] / x["risk_bp"]; rr.append(x)
    print(f"TF {m}m X2", f(pd.concat(rr)))
