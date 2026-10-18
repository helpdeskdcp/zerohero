"""Does the corrected Hybrid MP signal survive when traded as ATM option BUYING, on REAL option prices? (Upstox expired-contract 5m bars, NIFTY weekly,
2025-08-28..2026-10-13, ATM +/-12 strikes -> every weekday of the window is covered.) Each engine trade: long CE if the signal is long, long PE if short; nearest
weekly expiry (DTE>=0), ATM strike from the entry bar open; entry at the option bar OPEN of the entry bar, exit at the option bar CLOSE (and, as a bound, OPEN) of the
engine's exit bar. Single-exit engine models only (T2 / PINE) so one exit time exists. A trade with a missing option bar is dropped AND the underlying result of the
dropped trades is reported (selection-bias check). Costs: brokerage, STT on the sold premium, exchange, SEBI, stamp, GST + slippage per transaction (base 0.25 pt,
stress 0.5 pt). READ-ONLY.   Usage: python scripts/hybrid_mp_option_instrument_test.py"""
import datetime as dt
import sqlite3
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import corrected_params, fixed_params, resample, run  # noqa: E402
from app.research_engines.smart_bos.data import load_1m  # noqa: E402
from app.strategy_lab import edge_scan as ES  # noqa: E402

LOT = 65
DB = "data/historical/upstox/upstox_research.db"
IST = dt.timedelta(hours=5, minutes=30)
LINES = []


def say(s=""):
    print(s)
    LINES.append(s)


def cost_pts(entry, exit_, slip):
    brokerage = 2 * 20.0 / LOT
    txn = 0.000356 * (entry + exit_)
    return brokerage + 0.001 * exit_ + txn + 0.000001 * (entry + exit_) + 0.00003 * entry + 0.18 * (brokerage + txn) + 2 * slip


con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
expiries = [r[0] for r in con.execute("SELECT DISTINCT expiry FROM normalized_option_bars WHERE underlying='NIFTY' ORDER BY expiry")]
CACHE = {}


def bars_of(e):
    if e not in CACHE:
        d = {}
        for k, ty, ts, o, h, l, c in con.execute("SELECT strike, option_type, replace(substr(timestamp,1,16),'T',' '), open, high, low, close FROM normalized_option_bars "
                                                  "WHERE underlying='NIFTY' AND expiry=?", (e,)):
            d.setdefault((int(k), ty), {})[ts] = (o, h, l, c)
        CACHE[e] = d
    return CACHE[e]


b1 = load_1m("NIFTY", "INDEX", start="2025-06-01", end="2027-01-01", prefer="ANGELONE_CANDLES")
ch = resample(b1, 5, 555, 930)
t_ist = lambda i: dt.datetime.utcfromtimestamp(int(ch["t"][i])) + IST


def one(label, params):
    tr, _ = run(ch, params, b1=b1)
    tr = [t for t in tr if dt.date(1970, 1, 1) + dt.timedelta(days=t.day) >= dt.date(2025, 8, 28)]
    rows, dropped = [], []
    for t in tr:
        et, xt = t_ist(t.entry_i), t_ist(t.exit_i)
        d0 = et.date()
        exps = [e for e in expiries if dt.date.fromisoformat(e) >= d0]
        if not exps or t.exit_i == t.entry_i and False:
            dropped.append(t); continue
        e = exps[0]
        if (dt.date.fromisoformat(e) - d0).days > 6:
            dropped.append(t); continue
        spot = float(ch["o"][t.entry_i]); k = round(spot / 50) * 50
        ty = "CE" if t.side > 0 else "PE"
        bars = bars_of(e).get((k, ty), {})
        ek, xk = et.strftime("%Y-%m-%d %H:%M"), xt.strftime("%Y-%m-%d %H:%M")
        if ek not in bars or xk not in bars:
            dropped.append(t); continue
        ep, xpc, xpo = bars[ek][0], bars[xk][3], bars[xk][0]
        rows.append((t, ep, xpc, xpo, spot, (dt.date.fromisoformat(e) - d0).days))
    say(f"\n[{label}] engine trades in window {len(tr)}; priced on real option bars {len(rows)}; dropped (missing bar / no expiry) {len(dropped)}")
    if dropped:
        gd = np.array([t.bps for t in dropped]) - 2.0
        say(f"  dropped trades, underlying net at 2 bps: mean {gd.mean():+.2f} bps (kept: {np.mean([r[0].bps for r in rows]) - 2.0:+.2f})  <- selection-bias check")
    if not rows:
        return
    und = np.array([r[0].bps for r in rows])
    say(f"  underlying (same trades): gross {und.mean():+.2f} bps, net@2bps {und.mean() - 2:+.2f} bps, net@4bps {und.mean() - 4:+.2f} bps")
    for lab, idx in (("exit at bar CLOSE", 2), ("exit at bar OPEN (bound)", 3)):
        for slip in (0.25, 0.5):
            net = np.array([(r[idx] - r[1]) - cost_pts(r[1], r[idx], slip) for r in rows])
            gross = np.array([(r[idx] - r[1]) for r in rows])
            prem = np.array([r[1] for r in rows])
            d = np.array([r[0].day for r in rows])
            mu, p, n, nd = ES.cluster_t(d, net)
            say(f"  option {lab:26s} slip {slip:4.2f}/leg: gross {gross.mean():+6.2f} pts, cost {np.mean(gross - net):4.2f}, net {net.mean():+6.2f} pts/trade (p={p:.3f}), win {np.mean(net > 0) * 100:3.0f}%, "
                f"net/premium {np.mean(net / prem) * 100:+5.1f}%, net in bps of spot {np.mean(net / np.array([r[4] for r in rows])) * 1e4:+5.2f}, avg premium {prem.mean():.0f}, n={n}")


one("corrected, exit T2", corrected_params(chart_tf=5, exit_model="T2"))
one("corrected, exit PINE (SL or T4)", corrected_params(chart_tf=5, exit_model="PINE"))
one("previous fixed, exit T2 (for contrast)", fixed_params(chart_tf=5, exit_model="T2"))
open("data/research/strategy_lab/hybrid_option_instrument_2026-10-18.txt", "w").write("\n".join(LINES) + "\n")
