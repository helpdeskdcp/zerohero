"""read_contract._spot_rows: same rows, same order as the unbounded query, on data with the real write-order disorder; works through a temp view; falls back cleanly."""
import random
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.histcap import id_window as ID, read_contract as RC

Z = "%Y-%m-%dT%H:%M:%S.%fZ"
BASE = datetime(2026, 10, 27, 3, 45, tzinfo=timezone.utc).timestamp()


def _ts(e): return datetime.fromtimestamp(e, timezone.utc).strftime(Z)


def _build(path):
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE quote_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, received_ts TEXT NOT NULL, exch_ts TEXT, symbol TEXT, kind TEXT, source TEXT, expiry TEXT, ltp REAL, instrument_key TEXT);
        CREATE INDEX ix_quotes_lookup ON quote_snapshots(symbol, kind, exch_ts);""")
    rnd = random.Random(5); rows = []; w = BASE
    while w < BASE + 7 * 3600:
        lag = rnd.choice([0, 0, 0, 3, 40, 400, 1800, 6500])                                     # batch stamped when its cycle started, written `lag` later
        for _ in range(rnd.randint(3, 10)):
            w += rnd.uniform(0.3, 3.0)
            for sym, kind, ik in (("NIFTY", "INDEX", "NSE:99926000"), ("NIFTY", "FUTURE", "NFO:5"), ("SENSEX", "INDEX", "BSE:1"), ("NIFTY", "OPTION", "NFO:77")):
                rows.append((_ts(w - lag), _ts(w - lag - rnd.uniform(0, 2)), sym, kind, "ANGELONE_QUOTE_FULL", "29OCT2026", 100 + rnd.random(), ik))
    c.executemany("INSERT INTO quote_snapshots(received_ts, exch_ts, symbol, kind, source, expiry, ltp, instrument_key) VALUES (?,?,?,?,?,?,?,?)", rows); c.commit(); c.close()
    return BASE + 7 * 3600


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    p = str(tmp_path_factory.mktemp("s") / "m.db"); return p, _build(p)


def _old(conn, sym, kind, as_of, age):
    return [r["id"] for r in conn.execute(RC._SPOT_SQL.format(rng=""), (sym, "ANGELONE_QUOTE_FULL", kind, as_of, as_of, age, as_of)).fetchall()]


@pytest.mark.parametrize("sym,kind", [("NIFTY", "INDEX"), ("NIFTY", "FUTURE"), ("SENSEX", "INDEX")])
def test_same_rows_same_order(db, sym, kind):
    p, end = db; conn = sqlite3.connect(p); conn.row_factory = sqlite3.Row; RC._ID_CACHE.clear()
    for off in (0, 60, 900, 3600, 3 * 3600, 6.9 * 3600):
        for age in (30, 180, 1800):
            as_of_dt = datetime.fromtimestamp(BASE + off + 1800, timezone.utc); as_of = as_of_dt.isoformat()
            want = _old(conn, sym, kind, as_of, age)
            got = [r["id"] for r in RC._spot_rows(conn, sym, "ANGELONE_QUOTE_FULL", kind, as_of, age, as_of_dt)]
            assert got == want, (sym, kind, off, age)
    assert any(_old(conn, sym, kind, datetime.fromtimestamp(BASE + 3600, timezone.utc).isoformat(), 1800) for _ in [0])       # the comparison was not vacuous


def test_live_style_asof_after_the_last_row(db):
    p, end = db; conn = sqlite3.connect(p); conn.row_factory = sqlite3.Row; RC._ID_CACHE.clear()
    as_of_dt = datetime.fromtimestamp(end + 20, timezone.utc); as_of = as_of_dt.isoformat()
    assert [r["id"] for r in RC._spot_rows(conn, "NIFTY", "ANGELONE_QUOTE_FULL", "INDEX", as_of, 180, as_of_dt)] == _old(conn, "NIFTY", "INDEX", as_of, 180)
    assert ID.ceil_id(conn, end + 20, cache={}) is None                                          # nothing is stamped after end + lag: no upper bound


def test_works_through_a_temp_view_like_the_chain_reader(db):
    p, end = db; conn = sqlite3.connect(p); conn.row_factory = sqlite3.Row; RC._ID_CACHE.clear()
    conn.execute("CREATE TEMP VIEW quote_snapshots AS SELECT * FROM main.quote_snapshots WHERE julianday(received_ts) <= julianday('2026-10-27T10:00:00Z') AND source='ANGELONE_QUOTE_FULL'")
    as_of_dt = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc); as_of = as_of_dt.isoformat()
    want = _old(conn, "NIFTY", "INDEX", as_of, 1800)
    assert want and [r["id"] for r in RC._spot_rows(conn, "NIFTY", "ANGELONE_QUOTE_FULL", "INDEX", as_of, 1800, as_of_dt)] == want


def test_falls_back_to_the_unbounded_query_when_the_range_cannot_be_computed(db, monkeypatch):
    p, end = db; conn = sqlite3.connect(p); conn.row_factory = sqlite3.Row
    monkeypatch.setattr(ID, "floor_id", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("boom")))
    as_of_dt = datetime.fromtimestamp(BASE + 3600, timezone.utc); as_of = as_of_dt.isoformat()
    assert [r["id"] for r in RC._spot_rows(conn, "NIFTY", "ANGELONE_QUOTE_FULL", "INDEX", as_of, 1800, as_of_dt)] == _old(conn, "NIFTY", "INDEX", as_of, 1800)
