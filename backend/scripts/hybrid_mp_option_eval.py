"""Executable CE/PE view of the FIXED engine's NIFTY 5m index signals, kept separate from the index results.
PART A  MODEL (2022-01..2026-10): Black-Scholes premium path with a CONSTANT implied vol (sensitivity 12/14/16/20%), ATM strike, weekly expiry rule,
        spread/slippage/charges from option_model.Costs. Every number is a model estimate.
PART B  REAL BARS (Dhan rolling 1m option bars, 2026-08-03..08-17 only): the same signals priced from actual option OHLC. INSUFFICIENT SAMPLE by construction."""
import datetime as dt
import sqlite3
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import fixed_params, resample, run  # noqa: E402
from app.research_engines.hybrid_mp.option_model import (Costs, bs_greeks, bs_price, nifty_weekly_expiry, plan_option_trade,  # noqa: E402
                                                         round_trip_cost_rupees, years_to_expiry)
from app.research_engines.smart_bos.data import load_1m  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
LOT = 75                                    # ASSUMPTION: NIFTY lot size changed several times; confirm with the exchange before relying on rupee figures
C = Costs()
b1 = load_1m("NIFTY", "INDEX")
chart = resample(b1, 5, 555, 930)
tr, st = run(chart, fixed_params(chart_tf=5), b1=b1)
D = lambda day: dt.date.fromordinal(719163 + day)
print(f"index trades (fixed, 5m): {len(tr)} | assumptions: lot={LOT}, spread/side=Rs{C.half_spread}, slippage/side=Rs{C.slippage}, charges approx (brokerage Rs{C.brokerage_per_order}/order, STT {C.stt_sell_pct}% sell, exch {C.exchange_txn_pct}%)")


def model_pnl(t, sigma, spread=True):
    et = dt.datetime.fromtimestamp(int(chart["t"][t.entry_i]), IST)
    xt = dt.datetime.fromtimestamp(int(chart["t"][t.exit_i]), IST) + dt.timedelta(minutes=5)       # exit-bar close (approximation: exact minute inside the bar unknown)
    exp = nifty_weekly_expiry(et.date())
    T0 = years_to_expiry(et, exp)
    if T0 <= 0:
        return None
    call = t.side == 1
    K = round(t.entry / 50.0) * 50.0
    p0 = bs_price(t.entry, K, T0, sigma, call)
    if p0 < 1.0:
        return None
    g = bs_greeks(t.entry, K, T0, sigma, call)
    sp = (C.half_spread + C.slippage) if spread else 0.0
    entry_fill = p0 + sp
    T1 = years_to_expiry(xt, exp)
    pnl, last_exit = 0.0, entry_fill
    for w, px in t.parts:
        pe = bs_price(px, K, T1, sigma, call)
        last_exit = max(pe - sp, 0.05)
        pnl += w * (last_exit - entry_fill)
    charges = round_trip_cost_rupees(entry_fill, last_exit, 1, LOT, C) if spread else 0.0
    plan = plan_option_trade(12000.0, 1.0, entry_fill, LOT, g["delta"], g["gamma"], t.risk, t.side, costs=C)
    dte = (exp - et.date()).days
    return dict(pnl_unit=pnl, pct=pnl / entry_fill * 100.0, rs_lot=pnl * LOT - charges, entry=entry_fill, dte=dte, day=t.day, feasible=plan.feasible, reason=plan.reason, idx_r=t.r)


def summarize(rows, label):
    if not rows:
        print(f"  {label:28s} n=0"); return
    pct = np.array([r["pct"] for r in rows]); rs = np.array([r["rs_lot"] for r in rows])
    se = 1.96 * rs.std(ddof=1) / np.sqrt(len(rs)) if len(rs) > 1 else float("nan")
    print(f"  {label:28s} n={len(rows):5d} mean net Rs/lot {rs.mean():+9.1f} ±{se:6.1f} | mean % of premium {pct.mean():+6.2f}% | win {np.mean(rs>0)*100:4.1f}% | avg premium {np.mean([r['entry'] for r in rows]):6.1f} | index gross R {np.mean([r['idx_r'] for r in rows]):+.3f}")


print("\nPART A - MODEL (constant IV; BS repricing at the exit-bar close; partial exits priced at the final exit time)")
sel = [t for t in tr if D(t.day) >= dt.date(2022, 1, 3)]
for sigma in (0.12, 0.14, 0.16, 0.20):
    rows = [r for r in (model_pnl(t, sigma) for t in sel) if r]
    gross = [r for r in (model_pnl(t, sigma, spread=False) for t in sel) if r]
    print(f"\nIV {sigma*100:.0f}%:")
    summarize(gross, "no spread/slippage/charges")
    summarize(rows, "with spread, slippage, charges")
    if sigma == 0.14:
        for name, lo, hi in (("VALID 2022-2023", dt.date(2022, 1, 1), dt.date(2023, 12, 31)), ("OOS 2024-", dt.date(2024, 1, 1), dt.date(2027, 1, 1))):
            summarize([r for r in rows if lo <= D(r["day"]) <= hi], name)
        for name, a, b in (("DTE 0 (expiry day)", 0, 0), ("DTE 1", 1, 1), ("DTE 2", 2, 2), ("DTE 3-4", 3, 4), ("DTE 5+", 5, 99)):
            summarize([r for r in rows if a <= r["dte"] <= b], name)
        feas = np.mean([r["feasible"] for r in rows])
        from collections import Counter
        print(f"  capital Rs12,000 / 1% risk: 1 lot is within the risk budget and capital in {feas:.1%} of trades; reasons {dict(Counter(r['reason'] for r in rows))}")

print("\nPART B - REAL option bars (Dhan rolling, 1m), signals 2026-08-03..2026-08-17")
c = sqlite3.connect("file:data/hist_options.db?mode=ro", uri=True)
w0, w1 = dt.date(2026, 8, 3), dt.date(2026, 8, 17)
rows, miss = [], 0
inwin = [t for t in tr if w0 <= D(t.day) <= w1]
for t in inwin:
    et = dt.datetime.fromtimestamp(int(chart["t"][t.entry_i]), IST).astimezone(dt.timezone.utc)
    xt = (dt.datetime.fromtimestamp(int(chart["t"][t.exit_i]), IST) + dt.timedelta(minutes=4)).astimezone(dt.timezone.utc)
    otype = "CE" if t.side == 1 else "PE"
    r0 = c.execute("select strike, expiry, open, spot from hist_option_bars where timestamp=? and option_type=? order by abs(strike-?) limit 1",
                   (et.strftime("%Y-%m-%dT%H:%M:00+00:00"), otype, t.entry)).fetchone()
    if not r0:
        miss += 1; continue
    r1 = c.execute("select close from hist_option_bars where timestamp=? and option_type=? and strike=? and expiry=?",
                   (xt.strftime("%Y-%m-%dT%H:%M:00+00:00"), otype, r0[0], r0[1])).fetchone()
    if not r1 or not r0[2]:
        miss += 1; continue
    sp = C.half_spread + C.slippage
    ef, xf = r0[2] + sp, max(r1[0] - sp, 0.05)
    pnl = xf - ef
    rows.append(dict(pnl_unit=pnl, pct=pnl / ef * 100.0, rs_lot=pnl * LOT - round_trip_cost_rupees(ef, xf, 1, LOT, C), entry=ef, idx_r=t.r, day=t.day, dte=0, feasible=False, reason=""))
print(f"  index signals in window: {len(inwin)}; priced from real bars: {len(rows)}; no data for the contract/minute: {miss}")
summarize(rows, "REAL bars, net of costs")
print("  -> INSUFFICIENT SAMPLE (11 sessions): shown only to prove the pipeline prices real contracts; no conclusion may be drawn.")
