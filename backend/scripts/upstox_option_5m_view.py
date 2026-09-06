#!/usr/bin/env python3
"""
upstox_option_5m_view.py -- create/refresh a 5-minute VIEW over the imported
1-minute Upstox expired-options bars. NO data is copied; the view aggregates on
read.

READ-ONLY w.r.t. data: only a DROP/CREATE VIEW in the existing separate Upstox
research DB (data/historical/upstox/upstox_research.db). Does NOT touch
market_history.db, the Kaggle DB, normalized_option_bars rows, raw files, or any
trading / H1-H7 / spike logic.

View: option_bars_5m
  timestamp        -- IST 5-min bucket start (…+05:30), floor of the 1-min ts
  underlying, exchange, instrument_key, expiry, strike, option_type
  interval = '5minute'
  open  = open of the earliest 1-min bar in the bucket
  high  = MAX(high),  low = MIN(low)
  close = close of the latest 1-min bar
  volume= SUM(volume)               (real 1-min volumes summed)
  open_interest = OI of the latest 1-min bar in the bucket
  n_1m_bars     = how many 1-min bars fed this 5-min bar (1..5)
  first_1m_ts, last_1m_ts
  quality_flag  = OK | BAD_OHLC | NULL_PRICE   (recomputed on the 5-min bar)

No fabrication: a bucket with only 1 or 2 traded minutes yields a 5-min bar from
just those minutes and n_1m_bars records it.
"""
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "historical" / "upstox" / "upstox_research.db"
SRC = "upstox_expired_options"

# 5-min IST bucket: 'YYYY-MM-DDTHH' + ':' + floored-minute + ':00+05:30'
BKT = ("substr(timestamp,1,13) || ':' || "
       "printf('%02d', (CAST(substr(timestamp,15,2) AS INT)/5)*5) || ':00+05:30'")

# Design for index-friendliness: NO window functions, NO correlated subqueries.
# Each CTE is a plain GROUP BY that SQLite (>=3.35) push a `WHERE instrument_key=?`
# / `WHERE expiry=?` predicate down into, so per-contract / per-expiry queries use
# the (instrument_key, timestamp) index instead of scanning all ~5.4M 1-min rows.
#   hl        : MAX(high) / MIN(low) / SUM(volume) / COUNT(*)         -- pure aggregates
#   first_bar : MIN(timestamp) is the ONLY min/max in the query, so the bare
#               `open` column is taken from that same (earliest) 1-min row
#               -- SQLite's documented min/max bare-column behaviour
#   last_bar  : MAX(timestamp) likewise pins `close` / `open_interest` to the
#               latest 1-min row in the bucket
_W = f"source = '{SRC}'"
_BE = BKT.replace("timestamp", "e.timestamp")
VIEW_SQL = f"""
DROP VIEW IF EXISTS option_bars_5m;
CREATE VIEW option_bars_5m AS
WITH
hl AS (
  SELECT instrument_key, {BKT} AS bkt,
         underlying, exchange, expiry, strike, option_type,
         MAX(high) AS high, MIN(low) AS low, SUM(volume) AS volume,
         COUNT(*)  AS n_1m_bars
  FROM normalized_option_bars WHERE {_W}
  GROUP BY instrument_key, bkt, underlying, exchange, expiry, strike, option_type
),
first_bar AS (
  SELECT instrument_key, {BKT} AS bkt,
         MIN(timestamp) AS first_1m_ts,
         open           AS open          -- bare col <- earliest 1-min row
  FROM normalized_option_bars WHERE {_W}
  GROUP BY instrument_key, bkt
),
last_bar AS (
  SELECT instrument_key, {BKT} AS bkt,
         MAX(timestamp) AS last_1m_ts,
         close          AS close,        -- bare cols <- latest 1-min row
         open_interest  AS open_interest
  FROM normalized_option_bars WHERE {_W}
  GROUP BY instrument_key, bkt
)
SELECT
  hl.bkt AS timestamp, hl.underlying, hl.exchange, hl.instrument_key,
  hl.expiry, hl.strike, hl.option_type, '5minute' AS interval,
  fb.open, hl.high, hl.low, lb.close, hl.volume, lb.open_interest,
  hl.n_1m_bars, fb.first_1m_ts, lb.last_1m_ts,
  CASE
    WHEN fb.open IS NULL OR lb.close IS NULL OR hl.high IS NULL OR hl.low IS NULL THEN 'NULL_PRICE'
    WHEN hl.high < hl.low
      OR hl.high < fb.open - 1e-6 OR hl.high < lb.close - 1e-6
      OR hl.low  > fb.open + 1e-6 OR hl.low  > lb.close + 1e-6 THEN 'BAD_OHLC'
    ELSE 'OK'
  END AS quality_flag
FROM hl
JOIN first_bar fb ON fb.instrument_key = hl.instrument_key AND fb.bkt = hl.bkt
JOIN last_bar  lb ON lb.instrument_key = hl.instrument_key AND lb.bkt = hl.bkt;
"""


def main():
    if not DB.exists():
        print(f"[STOP] {DB} not found -- run the option import first.", file=sys.stderr)
        sys.exit(2)
    con = sqlite3.connect(DB)
    # Index on the base table so the view's per-contract / per-expiry queries use
    # an index range scan instead of a full 5.4M-row scan. Index only -- no rows
    # are added, changed or removed. Idempotent.
    con.execute("CREATE INDEX IF NOT EXISTS ix_nob_key_ts "
                "ON normalized_option_bars(instrument_key, timestamp)")
    con.executescript(VIEW_SQL)
    con.commit()
    q = con.execute

    print("view option_bars_5m created (no rows copied); ix_nob_key_ts ensured.")
    n5 = q("SELECT COUNT(*) FROM option_bars_5m").fetchone()[0]
    n1 = q(f"SELECT COUNT(*) FROM normalized_option_bars WHERE source='{SRC}'").fetchone()[0]
    print(f"  1-min source rows : {n1:,}")
    print(f"  5-min view rows   : {n5:,}   (~{n1/max(n5,1):.2f} 1-min bars per 5-min bar)")

    print("\n-- grid + quality on the view --")
    row = q("""SELECT
        SUM(substr(timestamp,15,2) NOT IN ('00','05','10','15','20','25','30','35','40','45','50','55')) off_grid,
        SUM(substr(timestamp,18,2)!='00') bad_sec,
        SUM(quality_flag!='OK') non_ok,
        SUM(n_1m_bars>5) impossible_gt5,
        MIN(timestamp), MAX(timestamp)
      FROM option_bars_5m""").fetchone()
    print(f"  off_5min_grid={row[0]}  bad_seconds={row[1]}  non_OK_flag={row[2]}  "
          f"n_1m>5={row[3]}  span {row[4]} .. {row[5]}")

    print("\n-- n_1m_bars distribution (how complete each 5-min bar is) --")
    for k, c in q("SELECT n_1m_bars, COUNT(*) FROM option_bars_5m GROUP BY 1 ORDER BY 1").fetchall():
        print(f"  {k} x 1-min : {c:,}")

    print("\n-- spot check: one 5-min bar vs its 1-min constituents --")
    ik, bkt = q("""SELECT instrument_key, timestamp FROM option_bars_5m
                   WHERE n_1m_bars=5 AND volume>0 ORDER BY timestamp DESC LIMIT 1""").fetchone()
    v = q("SELECT open,high,low,close,volume,open_interest FROM option_bars_5m "
          "WHERE instrument_key=? AND timestamp=?", (ik, bkt)).fetchone()
    print(f"  5m  {ik} {bkt}  O={v[0]} H={v[1]} L={v[2]} C={v[3]} V={v[4]} OI={v[5]}")
    for r in q(f"""SELECT timestamp,open,high,low,close,volume,open_interest
                   FROM normalized_option_bars WHERE source='{SRC}' AND instrument_key=?
                   AND {BKT}=? ORDER BY timestamp""", (ik, bkt)).fetchall():
        print(f"    1m  {r[0][11:19]}  O={r[1]} H={r[2]} L={r[3]} C={r[4]} V={r[5]} OI={r[6]}")
    con.close()


if __name__ == "__main__":
    main()
