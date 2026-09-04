"""
app/execution/ratelimit.py — token bucket, circuit breaker, bounded retry.
Uses a fake monotonic clock / fake sleep so these run instantly and
deterministically (no real time.sleep).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.execution.ratelimit import TokenBucket, CircuitBreaker, call_with_retry  # noqa: E402


class _FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def now(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def test_token_bucket_allows_up_to_burst_immediately(monkeypatch):
    clk = _FakeClock()
    monkeypatch.setattr("time.monotonic", clk.now)
    b = TokenBucket(rate_per_sec=1.0, burst=3)
    assert b.take(3, max_wait=0.01) is True     # burst capacity available instantly
    assert b.take(1, max_wait=0.0) is False     # exhausted, no time has passed


def test_token_bucket_refills_over_time(monkeypatch):
    clk = _FakeClock()
    monkeypatch.setattr("time.monotonic", clk.now)
    monkeypatch.setattr("time.sleep", lambda s: clk.advance(s))
    b = TokenBucket(rate_per_sec=10.0, burst=1)
    assert b.take(1, max_wait=0.01) is True
    assert b.take(1, max_wait=1.0) is True      # waits ~0.1s (simulated via fake sleep)


def test_token_bucket_gives_up_after_max_wait(monkeypatch):
    clk = _FakeClock()
    monkeypatch.setattr("time.monotonic", clk.now)
    monkeypatch.setattr("time.sleep", lambda s: clk.advance(s))
    b = TokenBucket(rate_per_sec=0.1, burst=1)   # very slow refill
    assert b.take(1, max_wait=0.01) is True
    assert b.take(1, max_wait=0.05) is False     # would need ~10s, way past max_wait


def test_circuit_breaker_closed_by_default():
    cb = CircuitBreaker(fail_threshold=3, reset_after=10)
    assert cb.state == "closed"
    assert cb.allow() is True


def test_circuit_breaker_opens_after_threshold(monkeypatch):
    clk = _FakeClock()
    monkeypatch.setattr("time.monotonic", clk.now)
    cb = CircuitBreaker(fail_threshold=2, reset_after=10)
    cb.record_failure()
    assert cb.state == "closed"
    cb.record_failure()
    assert cb.state == "open"
    assert cb.allow() is False


def test_circuit_breaker_success_resets_failure_count(monkeypatch):
    clk = _FakeClock()
    monkeypatch.setattr("time.monotonic", clk.now)
    cb = CircuitBreaker(fail_threshold=2, reset_after=10)
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    assert cb.state == "closed"   # only 1 consecutive failure since the reset


def test_circuit_breaker_half_opens_after_reset_window(monkeypatch):
    clk = _FakeClock()
    monkeypatch.setattr("time.monotonic", clk.now)
    cb = CircuitBreaker(fail_threshold=1, reset_after=5)
    cb.record_failure()
    assert cb.state == "open"
    clk.advance(6)
    assert cb.state == "half-open"
    assert cb.allow() is True


def test_call_with_retry_succeeds_first_try_no_sleep():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return {"status": "OK"}

    sleeps = []
    res = call_with_retry(fn, retries=3, sleep=sleeps.append)
    assert res == {"status": "OK"}
    assert calls["n"] == 1
    assert sleeps == []


def test_call_with_retry_retries_transient_status_then_succeeds():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return {"status": "OK"} if calls["n"] >= 2 else {"status": "ERROR"}

    sleeps = []
    res = call_with_retry(fn, retries=3, sleep=sleeps.append)
    assert res == {"status": "OK"}
    assert calls["n"] == 2
    assert len(sleeps) == 1


def test_call_with_retry_exhausts_and_returns_last_transient_result():
    def fn():
        return {"status": "ERROR"}

    sleeps = []
    res = call_with_retry(fn, retries=2, sleep=sleeps.append)
    assert res == {"status": "ERROR"}
    assert len(sleeps) == 2   # retries exhausted, gave up returning the last result


def test_call_with_retry_reraises_exception_after_exhausting_retries():
    def fn():
        raise ValueError("boom")

    sleeps = []
    try:
        call_with_retry(fn, retries=1, sleep=sleeps.append)
        assert False, "should have raised"
    except ValueError:
        pass
    assert len(sleeps) == 1


def test_call_with_retry_custom_is_retryable_stops_immediately():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "REJECTED"   # a real rejection -- must NOT be retried

    res = call_with_retry(fn, retries=3, is_retryable=lambda x: False, sleep=lambda s: None)
    assert res == "REJECTED"
    assert calls["n"] == 1
