"""INDEPENDENT recomputation of the option-instrument claims from the exported trade-level CSV (no engine, no run()): data fingerprints, underlying DIRECTION accuracy
(from spot, separate from option P&L), option P&L, an itemised cost recomputation, the 2x2 direction-vs-option-profit table, strike/expiry/DTE description, quote availability
and measured bid-ask spreads (Angel One quote_snapshots, only where they exist) and a bid/ask-executed P&L on that subset. READ-ONLY."""
import csv
import datetime as dt
import hashlib
import math
import sqlite3
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import resample  # noqa: E402
from app.research_engines.smart_bos.data import load_1m  # noqa: E402

LOT = 65
IST = dt.timedelta(hours=5, minutes=30)
FILE = sys.argv[1] if len(sys.argv) > 1 else "data/research/strategy_lab/option_instrument_trades_corrected_exit_t2.csv"
rows = list(csv.DictReader(open(FILE)))
sha = hashlib.sha256(open(FILE, "rb").read()).hexdigest()
print(f"FILE {FILE}\n  rows {len(rows)} sha256 {sha[:16]}")

# ---- data fingerprints
con = sqlite3.connect("file:data/market_history.db?mode=ro", uri=True, timeout=60)
src = con.execute("SELECT source, COUNT(*), MIN(substr(bar_start,1,10)), MAX(substr(bar_start,1,10)) FROM market_candles WHERE symbol='NIFTY' AND kind='INDEX' AND tf='1m' GROUP BY source").fetchall()
print("  spot data: data/market_history.db table market_candles, symbol NIFTY kind INDEX tf 1m:", src)
b1 = load_1m("NIFTY", "INDEX", start="2025-06-01", end="2027-01-01", prefer="ANGELONE_CANDLES")
ch = resample(b1, 5, 555, 930)
print(f"  used for this experiment: 1m rows {len(b1['t'])} (2025-06-01..), 5m bars {len(ch['t'])}, array sha256 {hashlib.sha256(np.ascontiguousarray(b1['c']).tobytes()).hexdigest()[:16]}")
uc = sqlite3.connect("file:data/historical/upstox/upstox_research.db?mode=ro", uri=True, timeout=60)
exps = sorted({r["expiry"] for r in rows})
q = uc.execute(f"SELECT COUNT(*), COUNT(DISTINCT expiry), MIN(substr(timestamp,1,10)), MAX(substr(timestamp,1,10)) FROM normalized_option_bars WHERE underlying='NIFTY' AND expiry IN ({','.join('?' * len(exps))})", exps).fetchone()
tot = uc.execute("SELECT COUNT(*) FROM normalized_option_bars WHERE underlying='NIFTY'").fetchone()[0]
print(f"  option data: data/historical/upstox/upstox_research.db table normalized_option_bars: total NIFTY rows {tot}; rows for the {len(exps)} expiries traded: {q[0]} ({q[2]}..{q[3]}); columns open/high/low/close/volume/open_interest, NO bid/ask")

# ---- spot-based direction accuracy (independent of the engine's exit logic)
t_open = {}
t_close = {}
for t, o, c in zip(ch["t"], ch["o"], ch["c"]):
    k = (dt.datetime.utcfromtimestamp(int(t)) + IST).strftime("%Y-%m-%d %H:%M")
    t_open[k], t_close[k] = float(o), float(c)
dirc, n_dir = [], 0
for r in rows:
    so, sc = t_open.get(r["entry_time_ist"]), t_close.get(r["exit_time_ist"])
    if so is None or sc is None:
        continue
    side = 1 if r["side"] == "long" else -1
    dirc.append(side * (sc - so) > 0)
dirc = np.array(dirc)
print(f"\nDIRECTION accuracy (sign of spot move from entry-bar open to exit-bar close agrees with the trade side): {dirc.mean() * 100:.1f}% of {len(dirc)} trades")

# ---- option P&L recomputed from the CSV prices with an itemised cost recomputation
ent = np.array([float(r["opt_entry_open"]) for r in rows]); xc = np.array([float(r["opt_exit_close"]) for r in rows])
und = np.array([float(r["underlying_gross_bps"]) for r in rows])
def cost(e, x, slip):
    brokerage = 2 * 20.0 / LOT; txn = 0.000356 * (e + x)
    return brokerage + 0.001 * x + txn + 0.000001 * (e + x) + 0.00003 * e + 0.18 * (brokerage + txn) + 2 * slip
gross = xc - ent
for slip in (0.25, 0.5):
    cst = np.array([cost(a, b, slip) for a, b in zip(ent, xc)]); net = gross - cst
    t = net.mean() / (net.std(ddof=1) / math.sqrt(len(net)))
    print(f"option exit at bar close, slip {slip}/leg: gross {gross.mean():+.3f} pts, cost {cst.mean():.3f} (brokerage {2 * 20 / LOT:.3f} + STT {np.mean(0.001 * xc):.3f} + txn/sebi/stamp/GST + slippage {2 * slip}), net {net.mean():+.3f} pts, t={t:.2f}, win {np.mean(net > 0) * 100:.1f}%")
print(f"underlying gross over the same trades: {und.mean():+.2f} bps (as reported by the engine run)")
cst_now = np.array([cost(a, b, 0.25) + (0.0015 - 0.001) * b for a, b in zip(ent, xc)])
print(f"  note: the experiment used STT 0.10% on the sold premium; the 2026-04-01 rate is 0.15% -> +{np.mean(0.0005 * xc):.3f} pts cost, net {np.mean(gross - cst_now):+.3f} pts (slip 0.25)")

print("\n2x2 table (direction correct vs option P&L > 0 before cost, exit at bar close):")
m = min(len(dirc), len(rows))
gp = gross[:m] > 0
for d_ in (True, False):
    sel = dirc[:m] == d_
    print(f"   direction {'CORRECT' if d_ else 'WRONG  '}: n={sel.sum():3d}, option gross>0 in {np.mean(gp[sel]) * 100:5.1f}%, mean option gross {gross[:m][sel].mean():+6.2f} pts")

print("\nstrike / expiry / DTE:")
dte = np.array([int(r["dte_days"]) for r in rows])
print("   DTE (days to expiry) counts:", dict(zip(*np.unique(dte, return_counts=True))), "| types:", {k: sum(r['type'] == k for r in rows) for k in ('CE', 'PE')},
      f"| ATM strike = round(spot/50)*50 at entry; avg entry premium {ent.mean():.1f} pts; expiries {len(exps)}")

# ---- quote availability + bid/ask-executed P&L on the Angel subset
MON = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
def label(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d.day:02d}{MON[d.month - 1]}{d.year}"
def quote(exp, strike, typ, tstr):
    t = dt.datetime.fromisoformat(tstr.replace(" ", "T")) - IST
    lo, hi = (t - dt.timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S"), (t + dt.timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S")
    rs = con.execute("SELECT received_ts, bid, ask FROM quote_snapshots WHERE symbol='NIFTY' AND kind='OPTION' AND expiry=? AND strike=? AND option_type=? AND received_ts>=? AND received_ts<=? AND bid>0 AND ask>=bid",
                     (label(exp), float(strike), typ, lo, hi)).fetchall()
    if not rs:
        return None
    return min(rs, key=lambda r: abs((dt.datetime.fromisoformat(r[0].replace("Z", "")) - t).total_seconds()))
sub, spreads, nets, nets_mid = [], [], [], []
first_q = con.execute("SELECT MIN(substr(received_ts,1,10)) FROM quote_snapshots WHERE symbol='NIFTY' AND kind='OPTION'").fetchone()[0]
for r in rows:
    if r["entry_time_ist"][:10] < first_q:
        continue
    sub.append(r)
    qe, qx = quote(r["expiry"], r["strike"], r["type"], r["entry_time_ist"]), quote(r["expiry"], r["strike"], r["type"], r["exit_time_ist"])
    if qe and qx:
        spreads += [qe[2] - qe[1], qx[2] - qx[1]]
        e_, x_ = qe[2], qx[1]                                              # BUY at the ask, SELL at the bid
        nets.append((x_ - e_) - (cost(e_, x_, 0.0)))                        # spread is already inside bid/ask: slippage 0
print(f"\nquote availability: Angel One quote_snapshots (bid/ask) start {first_q}; trades on/after that date {len(sub)}; with a bid/ask within +-3 min at BOTH entry and exit: {len(nets)}")
if nets:
    print(f"   measured ATM option spread: mean {np.mean(spreads):.2f} pts, median {np.median(spreads):.2f}, p90 {np.percentile(spreads, 90):.2f} (per leg-time; model assumed 0.25-0.5 pt slippage per leg per transaction)")
    print(f"   bid/ask-executed net P&L on that subset: mean {np.mean(nets):+.2f} pts per trade (n={len(nets)})")
print(f"   trades before {first_q}: {len(rows) - len(sub)} -> bid/ask UNAVAILABLE (Upstox bars are OHLC only): spread assumed, UNVERIFIED for those")
