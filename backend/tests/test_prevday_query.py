"""The 'previous captured session' lookup: identical answer to the old DISTINCT-and-sort query, and it walks the (session_date_ist, symbol) index instead of scanning every candle."""
import sqlite3

import pytest

OLD = ("SELECT DISTINCT session_date_ist FROM market_candles WHERE symbol=? AND source=? AND tf='5m' AND session_date_ist<? ORDER BY session_date_ist DESC")
NEW = ("SELECT session_date_ist FROM market_candles WHERE session_date_ist<? AND session_date_ist>=date(?, '-30 day') AND symbol=? AND source=? AND tf='5m' ORDER BY session_date_ist DESC LIMIT 1")


@pytest.fixture(scope="module")
def db():
    c = sqlite3.connect(":memory:")
    c.executescript("CREATE TABLE market_candles(id INTEGER PRIMARY KEY, symbol TEXT, source TEXT, kind TEXT, tf TEXT, bar_start TEXT, session_date_ist TEXT); CREATE INDEX ix_candles_day ON market_candles(session_date_ist, symbol);")
    rows = []
    for d in range(1, 29):
        day = f"2026-10-{d:02d}"
        for sym in ("NIFTY", "SENSEX", "NATURALGAS"):
            if sym == "SENSEX" and d in (20, 21, 22): continue                      # a symbol with a gap of three sessions
            if sym == "NATURALGAS" and d < 10: continue
            for tf in ("1m", "5m"):
                for k in range(40): rows.append((sym, "ANGELONE_CANDLE", "FUTURE", tf, f"{day}T04:{k:02d}:00Z", day))
    rows.append(("OLDSYM", "ANGELONE_CANDLE", "INDEX", "5m", "2026-08-01T04:00:00Z", "2026-08-01"))      # only candles far in the past
    c.executemany("INSERT INTO market_candles(symbol,source,kind,tf,bar_start,session_date_ist) VALUES (?,?,?,?,?,?)", rows); return c


@pytest.mark.parametrize("sym,before", [("NIFTY", "2026-10-28"), ("NIFTY", "2026-10-15"), ("SENSEX", "2026-10-23"), ("SENSEX", "2026-10-19"), ("NATURALGAS", "2026-10-12"), ("NATURALGAS", "2026-10-09"), ("NOSUCH", "2026-10-28"), ("NIFTY", "2026-09-01")])
def test_same_newest_earlier_session_as_the_old_query(db, sym, before):
    old = db.execute(OLD, (sym, "ANGELONE_CANDLE", before)).fetchall(); new = db.execute(NEW, (before, before, sym, "ANGELONE_CANDLE")).fetchall()
    assert [r[0] for r in new] == [r[0] for r in old[:1]], (sym, before)


def test_far_past_only_symbol_is_outside_the_30_day_floor_by_design(db):
    old = db.execute(OLD, ("OLDSYM", "ANGELONE_CANDLE", "2026-10-28")).fetchall(); new = db.execute(NEW, ("2026-10-28", "2026-10-28", "OLDSYM", "ANGELONE_CANDLE")).fetchall()
    assert old and not new                              # documented difference: a session older than 30 days is not 'the previous session'


def test_plan_walks_the_day_index(db):
    plan = " ".join(str(r[3]) for r in db.execute("EXPLAIN QUERY PLAN " + NEW, ("2026-10-28", "2026-10-28", "NIFTY", "ANGELONE_CANDLE")))
    assert "ix_candles_day" in plan and "TEMP B-TREE" not in plan
