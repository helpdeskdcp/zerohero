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
