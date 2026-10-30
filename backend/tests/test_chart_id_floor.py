"""Terminal chart queries with an id floor return exactly the candles the unfloored queries return, on data with the real write-order disorder (batches stamped up to ~2 h behind their id order, heartbeat rows with old exchange timestamps)."""
import random
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.arena import terminal as TM
from app.histcap import id_window as ID

IST = timezone(timedelta(hours=5, minutes=30))
DAY0 = datetime(2026, 10, 27, 0, 0, tzinfo=IST).timestamp()
Z = "%Y-%m-%dT%H:%M:%S.%fZ"


def _ts(e): return datetime.fromtimestamp(e, timezone.utc).strftime(Z)


@pytest.fixture(scope="module")
def dbfile(tmp_path_factory):
    p = str(tmp_path_factory.mktemp("d") / "m.db"); c = sqlite3.connect(p)
    c.executescript("""CREATE TABLE quote_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, received_ts TEXT NOT NULL, exch_ts TEXT, symbol TEXT, kind TEXT, source TEXT, expiry TEXT, strike REAL, option_type TEXT, ltp REAL, bid REAL, ask REAL, volume REAL);
        CREATE INDEX ix_quotes_opt ON quote_snapshots(symbol, expiry, strike, option_type, exch_ts); CREATE INDEX ix_quotes_lookup ON quote_snapshots(symbol, kind, exch_ts);""")
    rnd = random.Random(11); rows = []; w = DAY0 + 9.25 * 3600; px = 280.0
    while w < DAY0 + 15.5 * 3600:
        lag = rnd.choice([0, 0, 0, 2, 20, 300, 1500, 6800])                                  # the batch's received_ts = cycle start, written `lag` seconds later
        for k in range(rnd.randint(4, 14)):
            w += rnd.uniform(0.2, 2.5); rt = w - lag; px = max(1.0, px + rnd.uniform(-0.6, 0.6)); hb = rnd.random() < 0.3
            et = (w - lag - rnd.uniform(0, 3)) if not hb else (DAY0 + 9.3 * 3600)                    # heartbeat rows keep an OLD exchange timestamp but a recent received_ts
            for sym, kind, ex, st, ot in (("NATURALGAS", "OPTION", "29OCT2026", 290.0, "PE"), ("NATURALGAS", "OPTION", "29OCT2026", 300.0, "PE"), ("RELIANCE", "EQUITY", None, None, None)):
                rows.append((_ts(rt), _ts(et), sym, kind, "ANGELONE_QUOTE_FULL", ex, st, ot, round(px + (3 if st == 300.0 else 0), 2), px - 0.1, px + 0.1))
    c.executemany("INSERT INTO quote_snapshots(received_ts, exch_ts, symbol, kind, source, expiry, strike, option_type, ltp, bid, ask) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows); c.commit(); c.close()
    return p, DAY0 + 15.5 * 3600


def _run(fn, floor_on, monkeypatch, *a, **k):
    monkeypatch.setattr(TM, "_floor", (lambda c, lo, path: ID.floor_id(c, lo, cache={}, key=path)) if floor_on else (lambda c, lo, path: 0))
    return fn(*a, **k)


@pytest.mark.parametrize("tf", [1, 5])
def test_option_chart_identical_with_and_without_the_floor(dbfile, monkeypatch, tf):
    p, end = dbfile
    for since in (None, DAY0 + 10 * 3600, DAY0 + 12.7 * 3600, DAY0 + 15.4 * 3600):
        for now in (end, DAY0 + 13 * 3600):
            a = _run(TM.premium, False, monkeypatch, "NATURALGAS", "2026-10-29", 290.0, "PE", tf, since, now, p)["candles"]
            b = _run(TM.premium, True, monkeypatch, "NATURALGAS", "2026-10-29", 290.0, "PE", tf, since, now, p)["candles"]
            assert a == b and (since is not None or a), (tf, since, now)


def test_equity_chart_identical_with_and_without_the_floor(dbfile, monkeypatch):
    p, end = dbfile
    for since in (None, DAY0 + 11 * 3600, DAY0 + 14 * 3600):
        a = _run(TM.premium_equity, False, monkeypatch, "RELIANCE", 1, since, end, p)["candles"]; b = _run(TM.premium_equity, True, monkeypatch, "RELIANCE", 1, since, end, p)["candles"]
        assert a == b and (since is not None or len(a) > 30)


def test_the_floor_actually_removes_old_rows_and_never_a_wanted_one(dbfile):
    p, end = dbfile; c = sqlite3.connect(p); lo = DAY0 + 14.6 * 3600
    f = ID.floor_id(c, lo, cache={}, key="t"); assert f > 0
    wanted = c.execute("SELECT MIN(id) FROM quote_snapshots WHERE julianday(received_ts) >= julianday(?)", (_ts(lo),)).fetchone()[0]
    assert wanted > f                                                                           # every row with received_ts >= lo lies above the floor
    total = c.execute("SELECT COUNT(*) FROM quote_snapshots").fetchone()[0]; assert f > total * 0.25           # and a good part of the table is skipped (the floor trails the poll by the 3 h lag margin, so it bites late in the session and on earlier days' rows)


def test_floor_is_cached_per_bucket(dbfile):
    p, end = dbfile; c = sqlite3.connect(p); cache = {}; n = {"q": 0}
    ID.floor_id(c, DAY0 + 13 * 3600, cache=cache, key="k"); c.set_trace_callback(lambda s: n.__setitem__("q", n["q"] + 1)); ID.floor_id(c, DAY0 + 13 * 3600 + 120, cache=cache, key="k"); c.set_trace_callback(None)
    assert n["q"] == 0 and len(cache) == 1                                                       # same 5-minute bucket: no query at all
