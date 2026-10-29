"""EXPLORATORY breakdown (DEV window only, a new hypothesis if it suggests a filter): by entry-time bucket and by days-to-expiry for one configuration. python scripts/safe_ai_breakdown.py [thrust] [oi 0|1] [family]"""
import os, sys
from collections import defaultdict
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
from app.safe_ai import replay_hist as RH
th = float(sys.argv[1]) if len(sys.argv) > 1 else 0.005; oi = bool(int(sys.argv[2])) if len(sys.argv) > 2 else False; fam = sys.argv[3] if len(sys.argv) > 3 else "uturn"
idx, opt = RH.load(); tr, _ = RH.simulate(idx, opt, th, 0.005, until="2026-03-31", oi_filter=oi, family=fam)
def row(label, sub):
    s = RH.summarize(sub)
    if s.get("trades"): print(f"{label:22}{s['trades']:>4} trades  win {s['win_rate']:>4.0%}  net/trade {s['net_per_trade']:>7,.0f}  PF {s['profit_factor']}")
print(f"{fam} thrust {th * 100:.2f}% OI={oi}  DEV only ({len(tr)} trades)"); row("ALL", tr)
b = defaultdict(list)
for t in tr: h = int(t["t"][:2]); b["09:35-10:30" if (h == 9 or (h == 10 and int(t["t"][3:]) <= 30)) else "10:30-12:30" if h < 12 or (h == 12 and int(t["t"][3:]) <= 30) else "12:30-14:30"].append(t)
for k in sorted(b): row("entry " + k, b[k])
for k, f in (("expiry day (dte 0)", lambda t: t["dte"] == 0), ("dte 1", lambda t: t["dte"] == 1), ("dte 2+", lambda t: t["dte"] >= 2)): row(k, [t for t in tr if f(t)])
for k in ("CE", "PE"): row("side " + k, [t for t in tr if t["side"] == k])
