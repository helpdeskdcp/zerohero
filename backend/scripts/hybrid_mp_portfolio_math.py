"""Profit mathematics for the ONE measured edge (corrected Hybrid MP): fixed-fractional risk sizing, Kelly, diversification over NIFTY / BANKNIFTY / SENSEX, cost
sensitivity. Each trade risks a fraction f of CURRENT equity at its stop (position = f*equity / stop distance), so the trade's return on equity is f * netR with
netR = (gross bps - cost bps) / stop bps. Equity compounds trade by trade in time order (overlapping trades are rare: holding is ~1-2 hours). IN-SAMPLE: the rule
was chosen on this history, so treat the absolute numbers as an upper bound. READ-ONLY."""
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import corrected_params, resample, run  # noqa: E402
from app.research_engines.smart_bos.data import load_1m  # noqa: E402

LINES = []


def say(s=""):
    print(s)
    LINES.append(s)


TR = {}
for sym, start in (("NIFTY", "2017-01-01"), ("BANKNIFTY", "2021-01-01"), ("SENSEX", "2021-01-01")):
    b1 = load_1m(sym, "INDEX", start=start, end="2027-01-01", prefer="ANGELONE_CANDLES")
    ch = resample(b1, 5, 555, 930)
    tr, _ = run(ch, corrected_params(chart_tf=5), b1=b1)
    TR[sym] = [(int(ch["t"][t.entry_i]), int(ch["t"][t.exit_i]), t.bps, t.risk / t.entry * 1e4, t.day) for t in tr]


def stream(syms, since=None):
    ev = [(e, x, bps, rk, d, s) for s in syms for (e, x, bps, rk, d) in TR[s]]
    ev.sort()
    if since:
        ev = [v for v in ev if v[4] >= since]
    return ev


def equity(ev, f, cost, cap_conc=3):
    eq, peak, mdd = 1.0, 1.0, 0.0
    curve = []
    open_ = []
    day_ret = {}
    for e, x, bps, rk, d, s in ev:
        open_ = [y for y in open_ if y > e]
        if len(open_) >= cap_conc:
            continue                                                          # risk cap: never more than cap_conc simultaneous positions
        open_.append(x)
        r = f * (bps - cost) / rk                                             # return on equity
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
        day_ret[d] = day_ret.get(d, 0.0) + r
        curve.append((d, eq))
    return eq, mdd, day_ret, curve


def metrics(ev, f, cost):
    eq, mdd, day_ret, curve = equity(ev, f, cost)
    d0, d1 = ev[0][4], ev[-1][4]
    yrs = (d1 - d0) / 365.25
    dr = np.array(list(day_ret.values()))
    cagr = eq ** (1 / yrs) - 1 if eq > 0 else -1
    sharpe = dr.mean() / dr.std() * np.sqrt(252) if dr.std() > 0 else float("nan")
    return eq, cagr, mdd, sharpe, yrs, len(ev)


d_since = (dt.date(2021, 10, 1) - dt.date(1970, 1, 1)).days
for label, syms, since in (("NIFTY alone, 2017-2026", ["NIFTY"], None), ("NIFTY + BANKNIFTY + SENSEX, 2021-10..2026-10", ["NIFTY", "BANKNIFTY", "SENSEX"], d_since)):
    ev = stream(syms, since)
    say(f"\n================ {label}: {len(ev)} trades ================")
    say(f"{'cost':>5s} {'risk/trade f':>13s} | {'final equity':>12s} {'CAGR':>7s} {'maxDD':>7s} {'Sharpe':>6s}   (start = 1.00)")
    for cost in (2.0, 3.5):
        for f in (0.0025, 0.005, 0.01, 0.02):
            eq, cagr, mdd, sh, yrs, n = metrics(ev, f, cost)
            say(f"{cost:5.1f} {f * 100:12.2f}% | {eq:12.2f} {cagr * 100:6.1f}% {mdd * 100:6.1f}% {sh:6.2f}")
    # Kelly on the net R stream
    for cost in (2.0, 3.5):
        R = np.array([(bps - cost) / rk for (_, _, bps, rk, _, _) in ev])
        fs = np.linspace(0.0005, 0.12, 240)
        g = [np.mean(np.log1p(np.clip(f * R, -0.99, None))) for f in fs]
        fk = fs[int(np.argmax(g))]
        say(f"  Kelly (growth-optimal risk fraction) at cost {cost}: f* = {fk * 100:.1f}% of equity per trade (mean netR {R.mean():+.3f}, sd {R.std():.2f}); half-Kelly {fk * 50:.1f}%, quarter-Kelly {fk * 25:.1f}% -- estimation error makes full Kelly dangerous")
    R2 = np.array([(bps - 2.0) / rk for (_, _, bps, rk, _, _) in ev])
    lo = np.percentile([np.mean(np.random.default_rng(s).choice(R2, len(R2))) for s in range(2000)], [2.5, 97.5])
    say(f"  mean netR @2 bps = {R2.mean():+.3f} R/trade, 95% bootstrap [{lo[0]:+.3f}, {lo[1]:+.3f}] -> the edge is {'distinguishable from' if lo[0] > 0 else 'NOT distinguishable from'} zero")
say("\nREAD: (1) profit scales with risk per trade, drawdown scales faster; (2) the edge is tiny, so cost 3.5 bps (realistic futures) removes most of it; (3) these are in-sample numbers.")
open("data/research/strategy_lab/hybrid_portfolio_math_2026-10-18.txt", "w").write("\n".join(LINES) + "\n")
