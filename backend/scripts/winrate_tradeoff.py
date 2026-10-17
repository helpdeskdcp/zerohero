"""READ-ONLY. What does a high win rate COST? Zero-edge (every-N-bars, both directions) entries on REAL 5m data, bracket exit (stop / target in ATR units,
stop first if one bar touches both, 24-bar time exit), costs in bps. Shows how win rate, expectancy and the break-even win rate move with the target/stop ratio.
This is a property of the payoff geometry, independent of any strategy. Usage: python scripts/winrate_tradeoff.py"""
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import resample  # noqa: E402
from app.research_engines.smart_bos.data import ist_minutes, load_1m  # noqa: E402

H = 24            # 5m bars = 2 hours max hold
STEP = 7          # sample entries every 7th bar (reduces overlap)
OUT = []


def say(s=""):
    print(s)
    OUT.append(s)


def study(label, sym, kind, start, cost_bps, session):
    b1 = load_1m(sym, kind, start=start, end="2027-01-01", prefer="ANGELONE_CANDLES")
    ch = resample(b1, 5, session[0], session[1])
    o, h, l, c, t = ch["o"], ch["h"], ch["l"], ch["c"], ch["t"]
    days, _ = ist_minutes(t)
    n = len(c)
    tr = np.maximum(h - l, np.maximum(abs(h - np.r_[c[0], c[:-1]]), abs(l - np.r_[c[0], c[:-1]])))
    atr = np.convolve(tr, np.ones(14) / 14, mode="full")[:n]
    idx = np.arange(20, n - H - 2, STEP)
    idx = idx[(days[idx + H + 1] == days[idx]) & (atr[idx] > 0)]                    # whole window inside one session
    ent = o[idx + 1]
    W = lambda a: np.stack([a[idx + 1 + j] for j in range(H)], 1)                    # window of the next H bars starting at the entry bar
    hh, ll, cc = W(h), W(l), W(c)
    a = atr[idx]
    say(f"\n== {label}: {len(idx)} sampled entries x2 directions, ATR median {np.median(a / ent) * 1e4:.1f} bps, cost {cost_bps} bps round trip")
    say(f"{'stop(ATR)':>9} {'TP/SL':>6} | {'win%':>5} {'stopped%':>8} {'timeout%':>8} | {'avgW bps':>8} {'avgL bps':>8} | {'gross EV':>8} {'net EV':>8} | {'breakeven win%':>14}")
    for ks in (0.25, 0.5, 1.0, 2.0):
        for ratio in (0.1, 0.2, 0.33, 0.5, 1.0, 2.0, 3.0):
            kt = ks * ratio
            res = []
            for d in (1, -1):
                stop = ent - d * ks * a
                tgt = ent + d * kt * a
                hs = (ll <= stop[:, None]) if d == 1 else (hh >= stop[:, None])
                ht = (hh >= tgt[:, None]) if d == 1 else (ll <= tgt[:, None])
                fs = np.where(hs.any(1), hs.argmax(1), H + 9)
                ft = np.where(ht.any(1), ht.argmax(1), H + 9)
                tgt_first = ft < fs                                               # same bar => stop first (pessimistic)
                stop_first = fs <= ft
                tout = (fs == H + 9) & (ft == H + 9)
                pnl = np.where(tgt_first, d * (tgt - ent), np.where(stop_first & ~tout, d * (stop - ent), d * (cc[:, -1] - ent)))
                res.append((pnl / ent * 1e4, tgt_first, stop_first & ~tout, tout))
            pnl = np.concatenate([r[0] for r in res]); win = np.concatenate([r[1] for r in res]); st = np.concatenate([r[2] for r in res]); to = np.concatenate([r[3] for r in res])
            net = pnl - cost_bps
            w, lo = net[net > 0], net[net <= 0]
            wr = float((net > 0).mean())
            be = float(-lo.mean() / (w.mean() - lo.mean())) if len(w) and len(lo) else float("nan")
            say(f"{ks:9.2f} {ratio:6.2f} | {wr * 100:5.1f} {st.mean() * 100:8.1f} {to.mean() * 100:8.1f} | {w.mean() if len(w) else 0:8.1f} {lo.mean() if len(lo) else 0:8.1f} | {pnl.mean():8.2f} {net.mean():8.2f} | {be * 100:14.1f}")


study("NIFTY 5m (2024+)", "NIFTY", "INDEX", "2024-01-01", 2.0, (555, 930))
study("NATURALGAS fut 5m (2026)", "NATURALGAS", "FUTURE", "2026-01-01", 3.0, (540, 1410))
say("\nREAD: with ZERO edge, win% is set only by TP/SL: TP much smaller than SL => 85-95% wins, but each loss is 5-10x a win, costs eat the thin wins, net EV <= 0.")
say("A high win rate is only valuable when it comes from real predictive edge (win% above the break-even column). Win% alone says nothing about profit.")
open("data/research/strategy_lab/winrate_tradeoff_2026-10-18.txt", "w").write("\n".join(OUT) + "\n")
