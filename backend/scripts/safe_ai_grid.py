"""Pre-registered grid for the Safe AI on the 12-month NIFTY data: family x variant x spread, DEV (before 2026-04-01) and HOLD-OUT (after). Writes data/research/safe_ai/REPLAY_RESULTS.md. python scripts/safe_ai_grid.py"""
import json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
from app.safe_ai import replay_hist as RH
idx, opt = RH.load()
exps = sorted({k[0] for k in opt}); print("option expiries:", len(exps), exps[0], "->", exps[-1], "| index days:", len(idx))
GRID = [("uturn", 0.0035, False), ("uturn", 0.005, False), ("uturn", 0.007, False), ("uturn", 0.0035, True), ("uturn", 0.005, True), ("uturn", 0.007, True), ("sweep", 0.005, False), ("sweep", 0.005, True)]
SPREADS = [0.005]
SENS = [0.0025, 0.01]            # spread sensitivity, reported for every cell too
rows = []; allres = {}
for fam, th, oi in GRID:
    for hs in SPREADS + SENS:
        tr, sk = RH.simulate(idx, opt, th, hs, family=fam, oi_filter=oi)
        dev = RH.summarize([t for t in tr if t["day"] < RH.SPLIT], "dev"); ho = RH.summarize([t for t in tr if t["day"] >= RH.SPLIT], "hold-out"); al = RH.summarize(tr, "all")
        rows.append((fam + ("+OI" if oi else ""), th, hs, al, dev, ho)); allres[f"{fam}_{th}_{hs}_oi{int(oi)}"] = {"all": al, "dev": dev, "hold": ho, "skipped": sk}
def f(s):
    if not s.get("trades"): return "0 trades"
    return f"n={s['trades']} win {s['win_rate']:.0%} net ₹{s['net_rs']:,} (₹{s['net_per_trade']:,.0f}/trade, 95% [{s['ci95_per_trade'][0]:,}..{s['ci95_per_trade'][1]:,}]) PF {s['profit_factor']} worst ₹{s['worst']:,} maxDD ₹{s['max_drawdown']:,}"
L = ["# Safe AI — 12-month NIFTY backtest (Upstox 1-minute index + expired weekly option candles)", "", f"Option expiries in data: {len(exps)} ({exps[0]} → {exps[-1]}); index days {len(idx)}. Costs: full arena charges; slippage = half-spread each way (0.25 / 0.5 / 1.0 %). 1 lot. Pre-registered grid, nothing tuned after seeing results.", ""]
L += ["| family | thrust | half-spread | ALL | DEV (to 31-Mar-2026) | HOLD-OUT (from 1-Apr-2026) |", "|---|---|---|---|---|---|"]
for fam, th, hs, al, dev, ho in rows: L.append(f"| {fam} | {th * 100:.2f}% | {hs * 100:.2f}% | {f(al)} | {f(dev)} | {f(ho)} |")
open(os.path.join(HERE, "data", "research", "safe_ai", "REPLAY_RESULTS.md"), "w").write("\n".join(L) + "\n"); json.dump(allres, open(os.path.join(HERE, "data", "research", "safe_ai", "grid.json"), "w"), indent=1)
print("\n".join(L))
