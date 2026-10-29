"""A 'database is locked' write failure names the threads that were inside application code (so the holder of the write lock can be found from the journal)."""
import sqlite3
import threading
import time

import pytest

from app.histcap import store as ST


def test_locked_transaction_dumps_the_other_threads_once(tmp_path, capsys, monkeypatch):
    s = ST.HistStore(str(tmp_path / "h.db")); monkeypatch.setattr(ST, "_LAST_DUMP", [0.0])
    hold, release = threading.Event(), threading.Event()

    def holder():                                                     # a long write transaction on its own connection (what the real holder does)
        c = sqlite3.connect(s.path); c.execute("BEGIN IMMEDIATE"); c.execute("INSERT INTO capture_runs(started_ts, mode, market_state, auth_ok) VALUES ('x','HOLD','{}',1)"); hold.set(); release.wait(10); c.rollback(); c.close()
    t = threading.Thread(target=holder, name="long-writer"); t.start(); hold.wait(5)
    orig = s._conn
    def fast():                                                       # same connection, but not a 15 s wait in a unit test
        c = sqlite3.connect(s.path, check_same_thread=False, timeout=0.3); return c
    monkeypatch.setattr(s, "_conn", fast)
    with pytest.raises(sqlite3.OperationalError):
        with s.transaction() as conn: conn.execute("INSERT INTO capture_runs(started_ts, mode, market_state, auth_ok) VALUES ('y','WAIT','{}',1)")
    assert s._lock.acquire(timeout=1); s._lock.release()                 # the failed enter gave the python lock back (a leak would freeze every later capture)
    release.set(); t.join()
    out = capsys.readouterr().out; assert "DB LOCKED" in out and "txn by" in out


def test_dump_is_rate_limited(monkeypatch, capsys):
    monkeypatch.setattr(ST, "_LAST_DUMP", [0.0]); assert ST.dump_threads("a") is True and ST.dump_threads("b") is False
    assert ST._is_locked(sqlite3.OperationalError("database is locked")) and not ST._is_locked(ValueError("locked")) and not ST._is_locked(sqlite3.OperationalError("no such table"))


def test_a_read_then_write_transaction_survives_a_commit_by_another_connection(tmp_path):
    """put_raw reads (SELECT sha256) and then inserts. With a deferred BEGIN, a commit by ANOTHER connection between the two made the upgrade fail at once with 'database is locked' (WAL snapshot conflict, the busy
    timeout is not even consulted). The capture transaction now takes the write lock up front, so the other writer waits instead."""
    s = ST.HistStore(str(tmp_path / "h.db")); err = []; started = threading.Event()

    def other():
        started.wait(5)
        try:
            c = sqlite3.connect(s.path, timeout=10); c.execute("INSERT INTO capture_runs(started_ts, mode, market_state, auth_ok) VALUES ('o','OTHER','{}',1)"); c.commit(); c.close()
        except Exception as e: err.append(("other", e))
    t = threading.Thread(target=other); t.start()
    try:
        with s.transaction() as conn:
            conn.execute("SELECT id FROM raw_responses WHERE sha256='x'").fetchone(); started.set(); time.sleep(0.5)          # the other writer tries to commit right here
            conn.execute("INSERT INTO capture_runs(started_ts, mode, market_state, auth_ok) VALUES ('m','MAIN','{}',1)")
    except Exception as e: err.append(("main", e))
    t.join(); assert err == []
    c = sqlite3.connect(s.path); assert sorted(r[0] for r in c.execute("SELECT mode FROM capture_runs")) == ["MAIN", "OTHER"]
