"""
Rate limiting + circuit breaker + bounded retry.

Angel One publishes per-second and per-minute caps on order endpoints. We stay
well under them with a token bucket, and we NEVER run an uncontrolled retry
loop: `call_with_retry` is for idempotent reads only (order book, positions).
Order submission is deliberately NOT routed through it — the OrderManager
reconciles before it ever considers re-sending.
"""
from __future__ import annotations

import time
import threading


class TokenBucket:
    """Simple monotonic-clock token bucket. `take()` blocks (bounded by
    `max_wait`) until a token is available; returns False if it gave up."""

    def __init__(self, rate_per_sec: float = 3.0, burst: int = 5):
        self.rate = max(0.1, float(rate_per_sec))
        self.capacity = max(1, int(burst))
        self._tokens = float(self.capacity)
        self._ts = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self):
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._ts) * self.rate)
        self._ts = now

    def take(self, n: int = 1, max_wait: float = 5.0) -> bool:
        deadline = time.monotonic() + max_wait
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= n:
                    self._tokens -= n
                    return True
                deficit = n - self._tokens
                wait = deficit / self.rate
            if time.monotonic() + wait > deadline:
                return False
            time.sleep(min(wait, 0.25))


class CircuitBreaker:
    """Opens after `fail_threshold` consecutive failures; half-opens after
    `reset_after` seconds to let one probe through."""

    def __init__(self, fail_threshold: int = 4, reset_after: float = 30.0):
        self.fail_threshold = max(1, int(fail_threshold))
        self.reset_after = float(reset_after)
        self._fails = 0
        # None means "not open". A float 0.0 is a valid time.monotonic() reading
        # (early process uptime, or any clock stubbed for tests) and must never
        # be mistaken for "unset" -- that collision previously made the breaker
        # look permanently closed whenever it opened at t==0.0.
        self._open_since = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._open_since is None:
                return "closed"
            return "half-open" if (time.monotonic() - self._open_since) >= self.reset_after else "open"

    def allow(self) -> bool:
        return self.state != "open"

    def record_success(self):
        with self._lock:
            self._fails = 0
            self._open_since = None

    def record_failure(self):
        with self._lock:
            self._fails += 1
            if self._fails >= self.fail_threshold and self._open_since is None:
                self._open_since = time.monotonic()


def call_with_retry(fn, *, retries: int = 2, backoff_base: float = 0.4,
                    is_retryable=None, sleep=time.sleep):
    """Run `fn()` up to 1 + retries times with exponential backoff. For
    IDEMPOTENT calls only. `is_retryable(result_or_exc) -> bool` decides whether
    another attempt is worthwhile; default retries on any exception and on a
    result dict/obj whose status looks transient."""
    if is_retryable is None:
        def is_retryable(x):
            if isinstance(x, BaseException):
                return True
            st = getattr(x, "status", None) or (x.get("status") if isinstance(x, dict) else None)
            return st in ("ERROR", "UNKNOWN")

    last = None
    for attempt in range(retries + 1):
        try:
            res = fn()
            last = res
            if not is_retryable(res) or attempt == retries:
                return res
        except Exception as e:                     # noqa: BLE001 — deliberately broad
            last = e
            if attempt == retries:
                raise
        sleep(backoff_base * (2 ** attempt))
    return last
