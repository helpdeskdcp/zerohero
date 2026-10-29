"""Pre-registered Safe AI cells on BANKNIFTY (monthly expiry, days 0-2, ATM tolerance 100, 1 lot). Appends a BANKNIFTY section to data/research/safe_ai/REPLAY_RESULTS.md. python scripts/safe_ai_grid_bn.py"""
import json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
from app.safe_ai import replay_hist as RH
idx, opt = RH.load(und="BANKNIFTY"); exps = sorted({k[0] for k in opt}); print("BANKNIFTY option expiries:", len(exps), exps[:1], exps[-1:], "| index days", len(idx))
rows = []
def f(s):
    if not s.get("trades"): return "0 trades"
    return f"n={s['trades']} win {s['win_rate']:.0%} net ₹{s['net_rs']:,} (₹{s['net_per_trade']:,.0f}/trade, 95% [{s['ci95_per_trade'][0]:,}..{s['ci95_per_trade'][1]:,}]) PF {s['profit_factor']} worst ₹{s['worst']:,} maxDD ₹{s['max_drawdown']:,}"
for fam, th, oi in (("uturn", 0.0035, False), ("uturn", 0.005, False), ("uturn", 0.007, False), ("sweep", 0.005, False)):
    for hs in (0.005, 0.0025, 0.01):
        tr, sk = RH.simulate(idx, opt, th, hs, family=fam, oi_filter=oi, und="BANKNIFTY", atm_tol=100.0)
        rows.append(f"| {fam} | {th * 100:.2f}% | {hs * 100:.2f}% | {f(RH.summarize(tr))} | {f(RH.summarize([t for t in tr if t['day'] < RH.SPLIT]))} | {f(RH.summarize([t for t in tr if t['day'] >= RH.SPLIT]))} |")
out = ["", "## BANKNIFTY (monthly expiry; only the 3 sessions up to each month-end expiry qualify, so samples are small)", "", "| family | thrust | half-spread | ALL | DEV | HOLD-OUT |", "|---|---|---|---|---|---|"] + rows
p = os.path.join(HERE, "data", "research", "safe_ai", "REPLAY_RESULTS.md"); s = open(p).read()
if "## BANKNIFTY" in s: s = s[:s.index("\n## BANKNIFTY")]
open(p, "w").write(s.rstrip("\n") + "\n" + "\n".join(out) + "\n"); print("\n".join(out))
