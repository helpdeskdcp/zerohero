"""READ-ONLY reconciliation of MANUAL paper rows that mirror broker positions. The broker sync used to insert the broker's day-realised P&L again
on every sync (fixed 2026-10-15), so historical rows contain duplicates. This script never writes to the database; it prints the recorded total next
to an estimate (last closed row per token per day) and lists the exact duplicate pairs. Usage: python scripts/manual_pnl_reconcile.py [days=14]"""
import collections
import datetime as dt
import sqlite3
import sys

days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
c = sqlite3.connect("file:data/chanakya.db?mode=ro", uri=True)
c.row_factory = sqlite3.Row
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
day = lambda ts: dt.datetime.fromisoformat(ts).astimezone(IST).date().isoformat()
rows = c.execute("select id, trade_id, symboltoken tok, underlying, option_type ot, strike, entry, exit_price, quantity q, pnl, opened_ts, closed_ts, "
                 "substr(reason,1,26) reason from ai_paper_trades where strategy='MANUAL' and closed_ts is not null and opened_ts>=datetime('now', ?) "
                 "order by closed_ts", (f"-{days} days",)).fetchall()
by = collections.defaultdict(list)
for r in rows:
    by[day(r["closed_ts"])].append(r)
print("MANUAL paper rows: recorded vs de-duplicated estimate (estimate = last closed row per token per IST day; broker contract note is the truth)")
print(f"{'day':10s} {'rows':>5s} {'recorded':>11s} {'estimate':>11s} {'recorded-estimate':>14s}")
tr = te = 0.0
for d, rs in sorted(by.items()):
    rec = sum(r["pnl"] or 0 for r in rs)
    last = {}
    for r in rs:
        last[(r["tok"], r["underlying"], r["strike"], r["ot"])] = r
    est = sum(r["pnl"] or 0 for r in last.values())
    tr += rec; te += est
    print(f"{d:10s} {len(rs):5d} {rec:11,.0f} {est:11,.0f} {rec - est:14,.0f}")
print(f"{'TOTAL':10s} {len(rows):5d} {tr:11,.0f} {te:11,.0f} {tr - te:14,.0f}")
dups = []
for d, rs in by.items():
    seen = {}
    for r in rs:
        k = (r["tok"], r["entry"], r["exit_price"], r["q"], r["pnl"])
        if k in seen:
            dups.append((d, seen[k]["id"], r["id"], r["underlying"], r["strike"], r["ot"], r["pnl"]))
        else:
            seen[k] = r
print(f"\nexact duplicate rows (same token, entry, exit, qty, pnl on the same day): {len(dups)}; P&L counted twice: {sum(x[6] for x in dups):,.0f}")
for x in dups[:40]:
    print("  ", x)
