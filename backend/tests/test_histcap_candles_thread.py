"""Candles run on their own single-flight thread: the quote cycle never waits for the rate-limited candle calls; a pass still running when the next is due is skipped, not stacked."""
import threading
import time

from tests.test_histcap import _FakeSDK, _worker, store  # noqa: F401  (fixtures / helpers of the main histcap tests)


def test_a_slow_candle_pass_does_not_block_the_next_one_and_is_not_stacked(store, monkeypatch):
    w = _worker(store, monkeypatch, _FakeSDK()); gate = threading.Event(); calls = []
    monkeypatch.setattr(w, "_capture_candles", lambda *a, **k: (calls.append(1), gate.wait(3)))
    assert w._kick_candles() is True                                                          # started in the background
    t = time.time(); assert w._kick_candles() is False and time.time() - t < 0.2              # one is still running: skipped immediately, not queued
    assert w._cd_skipped == 1 and w._cd_running is True
    gate.set(); deadline = time.time() + 5
    while w._cd_running and time.time() < deadline: time.sleep(0.02)
    assert w._cd_running is False and w.last_candle_run["sec"] >= 0 and len(calls) >= 1
    assert w._kick_candles() is True                                                          # free again
    time.sleep(0.3)


def test_the_candle_pass_writes_candles_and_a_run_row(store, monkeypatch):
    w = _worker(store, monkeypatch, _FakeSDK()); w.cfg["candle_delay_sec"] = 0.0
    assert w._kick_candles(); deadline = time.time() + 8
    while w._cd_running and time.time() < deadline: time.sleep(0.02)
    assert w.last_candle_run and w.last_candle_run["candles"] >= 3
    with store._conn() as c: n = c.execute("SELECT COUNT(*) FROM capture_runs WHERE mode='CANDLES' AND ended_ts IS NOT NULL").fetchone()[0]
    assert n == 1 and store.summary()["candles"] >= 3


def test_run_once_with_candles_false_writes_no_candles_and_with_true_still_does(store, monkeypatch):
    w = _worker(store, monkeypatch, _FakeSDK())
    r = w.run_once("POLL_ONCE", do_candles=False); assert r["quotes"] >= 10 and r["candles"] == 0 and "candles" not in r["stage_s"]
    r2 = w.run_once("POLL_ONCE", do_candles=True); assert r2["candles"] >= 3                    # the inline path (CLI, tests, rollback flag) is unchanged
