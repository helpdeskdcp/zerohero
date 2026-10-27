"""id_window: the bounded-window 'newest row at or before a time' lookup returns exactly what the unbounded julianday/ORDER BY query returns, on a large synthetic capture table."""
import random
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.histcap import id_window as W

T0 = datetime(2026, 10, 26, 3, 45, tzinfo=timezone.utc)
OLD = ("SELECT received_ts FROM quote_snapshots WHERE symbol=? AND kind='OPTION' AND source='DHAN_CHAIN' AND julianday(received_ts)<=julianday(?) ORDER BY julianday(received_ts) DESC, id DESC LIMIT 1")
WHERE = "symbol=? AND kind='OPTION' AND source='DHAN_CHAIN' AND julianday(received_ts)<=julianday(?)"
EXISTS = ("SELECT 1 FROM quote_snapshots INDEXED BY sqlite_autoindex_quote_snapshots_1 WHERE source='DHAN_CHAIN' AND symbol=? AND kind='OPTION' LIMIT 1")


@pytest.fixture(scope="module")
def db():
    c = sqlite3.connect(":memory:")
    c.executescript("""CREATE TABLE quote_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, received_ts TEXT NOT NULL, symbol TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL, exchange TEXT, token TEXT, snap_key TEXT,
        UNIQUE(source, exchange, token, snap_key)); CREATE INDEX ix_quotes_lookup ON quote_snapshots(symbol, kind, received_ts);""")
    rnd = random.Random(7); t = T0; rows = []; n = 0
    for i in range(180_000):
        t += timedelta(milliseconds=rnd.randint(50, 900))                                           # strictly increasing capture clock, microsecond-shaped like the real column
        sym = rnd.choice(["NIFTY", "NIFTY", "SENSEX", "BANKNIFTY"]); src = "DHAN_CHAIN" if (i % 40 == 0 and sym != "BANKNIFTY") else "ANGELONE_QUOTE_FULL"
        rows.append((t.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), sym, "OPTION", src, "NSE", str(i), str(i)))
    c.executemany("INSERT INTO quote_snapshots(received_ts, symbol, kind, source, exchange, token, snap_key) VALUES (?,?,?,?,?,?,?)", rows)
    c.commit()
    return c, t


def cuts(t_end):
    span = (t_end - T0).total_seconds()
    return [(T0 + timedelta(seconds=f * span)).isoformat() for f in (0.9999, 0.9, 0.5, 0.123, 0.01)] + [t_end.isoformat().replace("+00:00", "Z"), "2026-10-26T03:45:00+00:00", "2030-01-01T00:00:00+00:00"]


@pytest.mark.parametrize("sym", ["NIFTY", "SENSEX", "BANKNIFTY"])
def test_same_answer_as_the_unbounded_query(db, sym, monkeypatch):
    db, t_end = db
    monkeypatch.setattr(W, "WINDOWS", (5_000, 30_000, 90_000))                                       # small windows so the widening path is exercised on 180 k rows
    for cut in cuts(t_end):
        want = db.execute(OLD, (sym, cut)).fetchone()
        got = W.newest_matching(db, "received_ts", WHERE, (sym, cut), cut, order="julianday(received_ts) DESC, id DESC", exists=(EXISTS, (sym,)))
        assert (got[0] if got else None) == (want[0] if want else None), (sym, cut)


def test_missing_symbol_returns_none_without_scanning(db):
    db, _ = db
    assert W.newest_matching(db, "id", WHERE, ("NOSUCH", "2030-01-01T00:00:00+00:00"), "2030-01-01T00:00:00+00:00", order="id DESC", exists=(EXISTS, ("NOSUCH",))) is None
    assert W.newest_matching(db, "id", WHERE, ("BANKNIFTY", "2030-01-01T00:00:00+00:00"), "2030-01-01T00:00:00+00:00", order="id DESC", exists=(EXISTS, ("BANKNIFTY",))) is None   # symbol exists but never from DHAN


def test_id_at_or_before_is_a_binary_search_on_the_primary_key(db):
    db, _ = db
    n = {"q": 0}; db.set_trace_callback(lambda s: n.__setitem__("q", n["q"] + 1))
    try: i = W.id_at_or_before(db, (T0 + timedelta(hours=2)).isoformat())
    finally: db.set_trace_callback(None)
    assert n["q"] < 40 and 0 < i < 180_000                                                           # ~18 point lookups, not a scan
    r = db.execute("SELECT received_ts FROM quote_snapshots WHERE id=?", (i,)).fetchone()[0]; nxt = db.execute("SELECT received_ts FROM quote_snapshots WHERE id=?", (i + 1,)).fetchone()[0]
    key = W._julian_str((T0 + timedelta(hours=2)).isoformat()); assert r <= key < nxt


def test_window_query_uses_the_rowid_range_not_the_symbol_index(db):
    db, _ = db
    plan = " ".join(str(x[3]) for x in db.execute("EXPLAIN QUERY PLAN SELECT received_ts FROM quote_snapshots NOT INDEXED WHERE id>? AND id<=? AND " + WHERE + " ORDER BY julianday(received_ts) DESC, id DESC LIMIT 1", (0, 10, "NIFTY", "2030-01-01")))
    assert "INTEGER PRIMARY KEY" in plan and "ix_quotes_lookup" not in plan
