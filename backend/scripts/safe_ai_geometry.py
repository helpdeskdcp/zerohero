"""EXPLORATORY (a new hypothesis, labelled as such): does the stop / first-target geometry change the picture? DEV window only (before 2026-04-01); the hold-out is not opened here. python scripts/safe_ai_geometry.py [thrust]"""
import os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
from app.arena.safe_ai import replay_hist as RH
th = float(sys.argv[1]) if len(sys.argv) > 1 else 0.005
idx, opt = RH.load(); print(f"thrust {th * 100:.2f}%, half-spread 0.5%, DEV only")
print(f"{'stop':>6}{'target':>8}{'n':>5}{'win':>6}{'net/trade':>11}{'PF':>6}  95% CI of net/trade")
for sl in (0.15, 0.25, 0.35):
    for t1 in (0.15, 0.20, 0.30, 0.50):
        tr, _ = RH.simulate(idx, opt, th, 0.005, until="2026-03-31", sl_frac=sl, t1_frac=t1); s = RH.summarize(tr)
        if s.get("trades"): print(f"{sl:>6.0%}{t1:>8.0%}{s['trades']:>5}{s['win_rate']:>6.0%}{s['net_per_trade']:>11,.0f}{str(s['profit_factor']):>6}  [{s['ci95_per_trade'][0]:,}..{s['ci95_per_trade'][1]:,}]")
