"""Full backtest report of the CORRECTED Hybrid MP variant (corrected_params) vs the previous fixed variant: per year, per instrument, cost sensitivity, drawdown,
and Rs per lot illustration. READ-ONLY. Costs are explicit; 'net' = gross - cost. Usage: python scripts/hybrid_mp_corrected_backtest.py"""
import collections
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import corrected_params, fixed_params, resample, run  # noqa: E402
from app.research_engines.smart_bos.data import load_1m  # noqa: E402
from app.strategy_lab import edge_scan as ES  # noqa: E402

LINES = []


def say(s=""):
    print(s)
    LINES.append(s)


def yr(day):
    return (dt.date(1970, 1, 1) + dt.timedelta(days=int(day))).year


def block(label, tr, cost, notional):
    tr = sorted(tr, key=lambda t: (t.day, t.entry_i))
    g = np.array([t.bps for t in tr]); net = g - cost
    eq = np.cumsum(net); dd = float((np.maximum.accumulate(eq) - eq).max())
    w, l = net[net > 0].sum(), -net[net <= 0].sum()
    mu, p, n, nd = ES.cluster_t(np.array([t.day for t in tr]), net)
    say(f"{label:28s} n={n:5d} win {np.mean(net > 0) * 100:4.1f}%  gross {g.mean():+5.2f}  net {net.mean():+5.2f} bps/trade (p={p:.3f})  PF {w / l if l else float('inf'):.2f}  total {net.sum():+8.0f} bps  maxDD {dd:6.0f} bps  ~Rs/lot total {net.sum() / 1e4 * notional:+10,.0f}")
    return tr, net


for sym, start, lot, spot in (("NIFTY", "2017-01-01", 65, 24000), ("BANKNIFTY", "2021-01-01", 30, 54000), ("SENSEX", "2021-01-01", 20, 80000)):
    b1 = load_1m(sym, "INDEX", start=start, end="2027-01-01", prefer="ANGELONE_CANDLES")
    ch = resample(b1, 5, 555, 930)
    say(f"\n================ {sym} 5m, {start[:4]}-2026, lot {lot} @ ~{spot} (Rs illustration only), cost 2 bps ================")
    res = {}
    for name, pr in (("previous (fixed, min_risk_atr 0.5)", fixed_params(chart_tf=5)), ("CORRECTED (min_risk_atr 2.0)", corrected_params(chart_tf=5))):
        tr, _ = run(ch, pr, b1=b1)
        res[name] = tr
        block(name, tr, 2.0, spot * lot)
    tr = sorted(res["CORRECTED (min_risk_atr 2.0)"], key=lambda t: (t.day, t.entry_i))
    say("  corrected, per year (net bps/trade at 2 bps cost):")
    by = collections.defaultdict(list)
    for t in tr:
        by[yr(t.day)].append(t.bps - 2.0)
    say("   " + "  ".join(f"{y}: {np.mean(v):+5.1f} (n={len(v)})" for y, v in sorted(by.items())))
    say("  corrected, cost sensitivity (net bps/trade):  " + "   ".join(f"cost {c:.0f} bps: {np.mean([t.bps for t in tr]) - c:+5.2f}" for c in (1, 2, 3, 4, 5)))
    g = np.array([t.bps for t in tr]); net = g - 2.0
    say(f"  corrected: avg win {net[net > 0].mean():+.1f} bps, avg loss {net[net <= 0].mean():+.1f} bps, worst trade {net.min():+.0f} bps, best {net.max():+.0f} bps, trades/year ~{len(tr) / max(1, len(by)):.0f}")
open("data/research/strategy_lab/hybrid_corrected_backtest_2026-10-18.txt", "w").write("\n".join(LINES) + "\n")
