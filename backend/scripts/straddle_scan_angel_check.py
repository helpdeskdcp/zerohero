"""INDEPENDENT-SOURCE check of the expiry-structure survivors using the Angel One option quotes captured by histcap (quote_snapshots, real bid/ask),
on the NIFTY expiries 2026-09-08 .. 2026-10-13. Execution is conservative: SELL at the bid, BUY at the ask (the spread IS the slippage). A missing quote
within +/-3 minutes of the target time = no trade. Compared with the Upstox-bar results for the same expiries. READ-ONLY."""
import csv
import datetime as dt
import sqlite3
import sys

import numpy as np

sys.path.insert(0, ".")
from app.strategy_lab import straddle_scan as SS  # noqa: E402

con = sqlite3.connect("file:data/market_history.db?mode=ro", uri=True, timeout=60)
IST = dt.timedelta(hours=5, minutes=30)
MON = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
HY = [h for h in SS.HYPS if h.name in ("short strangle1 09:20->1515", "short straddle 09:20->1515", "short straddle 10:30->1515", "short straddle E-1 14:45->E 09:30")]
expiries = sorted({r[0] for r in con.execute("SELECT DISTINCT expiry FROM quote_snapshots WHERE symbol='NIFTY' AND kind='OPTION'")}, key=lambda s: dt.date(int(s[-4:]), MON[s[2:5]], int(s[:2])))
sessions = [r[0] for r in con.execute("SELECT DISTINCT session_date_ist FROM quote_snapshots WHERE symbol='NIFTY' AND kind='INDEX' ORDER BY 1")]


def utc_bounds(day, hhmm, tol_min=3):
    t = dt.datetime.fromisoformat(f"{day}T{hhmm}:00") - IST
    return (t - dt.timedelta(minutes=tol_min)).strftime("%Y-%m-%dT%H:%M:%S"), (t + dt.timedelta(minutes=tol_min)).strftime("%Y-%m-%dT%H:%M:%S"), t


def quote(exp, strike, typ, day, hhmm):
    lo, hi, t = utc_bounds(day, hhmm)
    rows = con.execute("SELECT received_ts, bid, ask, ltp FROM quote_snapshots WHERE symbol='NIFTY' AND kind='OPTION' AND expiry=? AND strike=? AND option_type=? "
                       "AND received_ts>=? AND received_ts<=? AND bid>0 AND ask>0 AND ask>=bid", (exp, strike, typ, lo, hi)).fetchall()
    if not rows:
        return None
    best = min(rows, key=lambda r: abs((dt.datetime.fromisoformat(r[0].replace("Z", "")) - t).total_seconds()))
    return best[1], best[2]


def spot(day, hhmm):
    lo, hi, t = utc_bounds(day, hhmm)
    rows = con.execute("SELECT received_ts, ltp FROM quote_snapshots WHERE symbol='NIFTY' AND kind='INDEX' AND received_ts>=? AND received_ts<=?", (lo, hi)).fetchall()
    return min(rows, key=lambda r: abs((dt.datetime.fromisoformat(r[0].replace("Z", "")) - t).total_seconds()))[1] if rows else None


ups = {}
for r in csv.DictReader(open("data/research/strategy_lab/straddle_scan_2026-10-18_strict_trades.csv")):
    ups[(r["expiry"], r["hypothesis"])] = r
print(f"{'expiry':10s} {'hypothesis':36s} | Angel(bid/ask) gross net | spread paid | Upstox(5m bars) gross net")
res = {h.name: [] for h in HY}
for e in expiries:
    d = dt.date(int(e[-4:]), MON[e[2:5]], int(e[:2])).isoformat()
    if d not in sessions or d >= "2026-10-14":
        continue
    prev = sessions[sessions.index(d) - 1]
    for h in HY:
        eday = d if h.entry_day == 0 else prev
        s = spot(eday, h.entry)
        if s is None:
            continue
        atm = round(s / 50) * 50
        kc, kp = (atm, atm) if h.legs == "straddle" else (atm + 50, atm - 50)
        xt = SS._hhmm_add(h.exit, 0)
        eC, eP = quote(e, float(kc), "CE", eday, h.entry), quote(e, float(kp), "PE", eday, h.entry)
        xC, xP = quote(e, float(kc), "CE", d, xt), quote(e, float(kp), "PE", d, xt)
        if None in (eC, eP, xC, xP):
            print(f"{d} {h.name:36s} | no quote within +-3 min")
            continue
        entry_recv = eC[0] + eP[0]; exit_pay = xC[1] + xP[1]            # sell at bid, buy at ask
        gross = entry_recv - exit_pay
        spread_paid = (eC[1] - eC[0]) + (eP[1] - eP[0]) + (xC[1] - xC[0]) + (xP[1] - xP[0])
        cost = SS.cost_points("short", entry_recv, exit_pay) - 4 * SS.SLIP      # statutory/brokerage only: slippage is already inside bid/ask
        net = gross - cost
        u = ups.get((d, h.name))
        res[h.name].append((d, net))
        print(f"{d} {h.name:36s} | {gross:7.1f} {net:7.1f} | {spread_paid:6.1f} | " + (f"{float(u['gross_pts']):7.1f} {float(u['net_pts']):7.1f}" if u else "n/a"))
print("\nsummary (Angel bid/ask execution):")
for h in HY:
    v = [x[1] for x in res[h.name]]
    if v:
        mu, t, p = SS.tstat_one_sided(v)
        print(f"  {h.name:36s} n={len(v)} mean net {mu:+7.1f} pts  win {np.mean(np.array(v) > 0) * 100:3.0f}%  worst {min(v):+7.1f}  t={t:.2f} p={p:.3f}")
