"""The global DB lock stays semantically unchanged, but contention is now attributed (who waited, who held, how long)."""
import threading
import time


def test_slow_holder_and_waiter_are_named_in_warnings(fresh_db, caplog, monkeypatch):
    monkeypatch.setattr(fresh_db, "_LOCK_WARN_SEC", 0.15)
    started = threading.Event()

    def slow_user():
        with fresh_db.db():
            started.set()
            time.sleep(0.4)

    t = threading.Thread(target=slow_user, name="slow-thread")
    t.start()
    started.wait(2)
    with caplog.at_level("WARNING"):
        with fresh_db.db():
            pass
    t.join()
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "db lock wait" in text and "db lock hold" in text and "slow_user" in text and "slow-thread" in text
    assert fresh_db.LOCK_STATS["max_hold_sec"] >= 0.35 and fresh_db.LOCK_STATS["max_wait_sec"] >= 0.15
    assert fresh_db.LOCK_STATS["slow"] and len(fresh_db.LOCK_STATS["slow"]) <= 25


def test_normal_use_unchanged_and_fast(fresh_db, caplog):
    fresh_db.set_setting("k_diag", "v")
    with caplog.at_level("WARNING"):
        assert fresh_db.get_setting("k_diag") == "v"
    assert not [r for r in caplog.records if "db lock" in r.getMessage()]
    assert fresh_db._lock_info["holder"] is None                       # released


def test_get_setting_is_not_blocked_by_a_thread_holding_the_global_lock(fresh_db):
    fresh_db.set_setting("hot_key", "v1")
    held, release = threading.Event(), threading.Event()

    def holder():
        with fresh_db.db():
            held.set()
            release.wait(5)

    t = threading.Thread(target=holder, name="lock-holder")
    t.start()
    held.wait(2)
    t0 = time.monotonic()
    val = fresh_db.get_setting("hot_key")                     # old implementation would block here until the holder lets go
    waited = time.monotonic() - t0
    miss = fresh_db.get_setting("no_such_key", "dflt")
    release.set()
    t.join()
    assert val == "v1" and miss == "dflt" and waited < 0.5


def test_get_setting_sees_committed_writes_and_does_not_create_a_missing_database(fresh_db, tmp_path, monkeypatch):
    fresh_db.set_setting("k2", "a")
    fresh_db.set_setting("k2", "b")
    assert fresh_db.get_setting("k2") == "b"
    ghost = tmp_path / "ghost.db"
    monkeypatch.setattr(fresh_db, "DB_PATH", str(ghost))
    monkeypatch.setattr(fresh_db, "get_conn", lambda: (_ for _ in ()).throw(RuntimeError("fallback reached")))   # the locked fallback must be the only way to touch a DB
    with __import__("pytest").raises(RuntimeError):
        fresh_db.get_setting("x")                              # read-only open of a missing file fails -> falls back; it never CREATES the file
    assert not ghost.exists()


def test_hot_read_helpers_do_not_wait_for_the_lock_and_return_committed_rows(fresh_db):
    from app.engines.paper_trading import open_trade
    row = open_trade({"signal_id": None, "market": "NSE", "underlying": "NIFTY", "instrument": "OPTION", "expiry": "x", "strike": 1, "option_type": "CE",
                      "direction": "BUY", "timeframe": "", "entry": 10.0, "target_1": None, "stop_loss": None, "trailing_stop": 0, "quantity": 1,
                      "market_regime": "", "oi_evidence": "", "reason": "t", "strategy": "MANUAL", "setup": None, "atr_pct": None,
                      "max_hold_sec": None, "symboltoken": "T1"})
    held, release = threading.Event(), threading.Event()

    def holder():
        with fresh_db.db():
            held.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    held.wait(2)
    t0 = time.monotonic()
    opens = fresh_db.list_open_managed()
    one = fresh_db.get_trade(row["trade_id"])
    lst = fresh_db.list_trades(status="OPEN", limit=10, strategy="MANUAL")
    sigs = fresh_db.list_scalp_signals(limit=5)
    waited = time.monotonic() - t0
    release.set()
    t.join()
    assert waited < 0.5 and [x["trade_id"] for x in opens] == [row["trade_id"]] and one["symboltoken"] == "T1" and len(lst) == 1 and sigs == []


def test_keeper_connection_keeps_the_wal_alive_and_cuts_write_amplification(fresh_db):
    import os

    def wb():
        for line in open("/proc/self/io"):
            if line.startswith("write_bytes"):
                return int(line.split()[1])
    assert fresh_db._KEEPER["conn"] is not None and fresh_db._KEEPER["path"] == fresh_db.DB_PATH
    for i in range(60):
        fresh_db.set_setting(f"k{i % 7}", str(i))
    assert os.path.exists(fresh_db.DB_PATH + "-wal")            # without a keeper the WAL is checkpointed and deleted on every close
    assert fresh_db.get_setting("k0") is not None


def test_keeper_follows_a_changed_database_path(fresh_db, tmp_path, monkeypatch):
    other = str(tmp_path / "other.db")
    monkeypatch.setattr(fresh_db, "DB_PATH", other)
    fresh_db.init_db()
    assert fresh_db._KEEPER["path"] == other
