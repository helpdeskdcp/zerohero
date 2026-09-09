#!/usr/bin/env python3
"""
One-off backfill: materialise `quote_snapshots.oi_change` for rows the histcap
worker captured before the derive-at-write change (commit 2812910).

AngelOne's market/v1/quote sends no change-in-OI, so oi_change was NULL for every
OPTION/FUTURE row. This computes the standard day-over-day value:

    oi_change = current_oi  -  the contract's last OI from any earlier session (oi > 0)

Rows updated are tagged `doi_derived` in `flags`. Idempotent: only touches rows
with `oi_change IS NULL AND oi > 0`, so re-running is a no-op. Commits per symbol
(each transaction is < 1 s) so the live capture writer is never blocked long;
WAL mode means readers never block at all.

    ./venv/bin/python scripts/backfill_oi_change.py [--date YYYY-MM-DD] [--dry-run]

Default date = the latest session_date_ist present.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_DEF_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "market_history.db")

_BASELINE_SQL = """
SELECT q.expiry, q.strike, q.option_type, q.oi
FROM quote_snapshots q
JOIN (
  SELECT COALESCE(expiry,'') e, COALESCE(strike,-1) s, COALESCE(option_type,'') o,
         MAX(received_ts) mts
  FROM quote_snapshots
  WHERE symbol=? AND kind IN ('OPTION','FUTURE') AND session_date_ist<? AND oi IS NOT NULL AND oi>0
  GROUP BY COALESCE(expiry,''), COALESCE(strike,-1), COALESCE(option_type,'')
) m ON COALESCE(q.expiry,'')=m.e AND COALESCE(q.strike,-1)=m.s
   AND COALESCE(q.option_type,'')=m.o AND q.received_ts=m.mts
WHERE q.symbol=? AND q.kind IN ('OPTION','FUTURE')
"""


def _with_flag(flags: str | None) -> str:
    if not flags:
        return "doi_derived"
    return flags if "doi_derived" in flags.split(",") else flags + ",doi_derived"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("CHANAKYA_HIST_DB_PATH", _DEF_DB))
    ap.add_argument("--date", default=None, help="session_date_ist to backfill (default: latest)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    con = sqlite3.connect(a.db, timeout=20)
    con.execute("PRAGMA busy_timeout=20000")

    day = a.date or con.execute(
        "SELECT MAX(session_date_ist) FROM quote_snapshots WHERE kind IN ('OPTION','FUTURE')"
    ).fetchone()[0]
    if not day:
        print("no OPTION/FUTURE rows found")
        return 1
    syms = [r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM quote_snapshots "
        "WHERE session_date_ist=? AND kind IN ('OPTION','FUTURE') ORDER BY symbol", (day,))]

    print(f"backfill oi_change  db={a.db}  date={day}  symbols={syms}"
          + ("  [DRY RUN]" if a.dry_run else ""))
    grand = 0
    for sym in syms:
        base: dict = {}
        for expiry, strike, ot, oi in con.execute(_BASELINE_SQL, (sym, day, sym)):
            base[(expiry or "", strike, ot)] = oi
        pending = con.execute(
            "SELECT id, expiry, strike, option_type, oi, flags FROM quote_snapshots "
            "WHERE symbol=? AND session_date_ist=? AND kind IN ('OPTION','FUTURE') "
            "AND oi_change IS NULL AND oi>0", (sym, day)).fetchall()
        ups = []
        no_base = 0
        for rid, expiry, strike, ot, oi, flags in pending:
            b = base.get((expiry or "", strike, ot))
            if b is None:
                no_base += 1
                continue
            ups.append((round(oi - b, 0), _with_flag(flags), rid))
        if ups and not a.dry_run:
            con.executemany("UPDATE quote_snapshots SET oi_change=?, flags=? WHERE id=?", ups)
            con.commit()
        grand += len(ups)
        print(f"  {sym:12} candidates={len(pending):>7}  filled={len(ups):>7}  "
              f"no_prior_baseline={no_base:>6}  baseline_contracts={len(base)}")
    print(f"TOTAL filled: {grand}" + ("  (dry run — nothing written)" if a.dry_run else ""))
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
