"""Bounded, batched, recoverable candle persistence into its OWN SQLite file.

* offer() never blocks the live path: if the bounded queue is full it returns False, counts the
  rejection and calls on_reject (the caller decides what to do) -- nothing is silently discarded.
* a batch that fails to write is KEPT and retried (bounded by max_pending); it is only reported
  as lost if that bound is exceeded, with counters and the callback.
* closed candles are immutable: INSERT OR IGNORE on UNIQUE(instrument, tf_sec, start_ts) makes
  replays/retries idempotent.
* retention uses bounded, indexed chunk deletes (never one long lock like the old raw_responses incident).
"""
from __future__ import annotations

import os
import queue
import sqlite3
import threading
import time
from collections import deque

from .. import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
  instrument TEXT NOT NULL, tf_sec INTEGER NOT NULL, start_ts INTEGER NOT NULL, provider TEXT NOT NULL,
  o REAL NOT NULL, h REAL NOT NULL, l REAL NOT NULL, c REAL NOT NULL, tick_count INTEGER NOT NULL,
  volume REAL, volume_complete INTEGER NOT NULL, oi REAL, oi_change REAL, first_ts REAL, last_ts REAL,
  PRIMARY KEY (instrument, tf_sec, start_ts));
CREATE INDEX IF NOT EXISTS ix_candles_ts ON candles(start_ts);
CREATE TABLE IF NOT EXISTS gap_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, instrument TEXT NOT NULL, after_ts INTEGER NOT NULL,
  before_ts INTEGER NOT NULL, seconds INTEGER NOT NULL, provider TEXT NOT NULL, reconciled INTEGER NOT NULL DEFAULT 0,
  UNIQUE(instrument, after_ts, before_ts));
"""
_COLS = ("instrument", "tf_sec", "start_ts", "provider", "o", "h", "l", "c", "tick_count", "volume",
         "volume_complete", "oi", "oi_change", "first_ts", "last_ts")


def estimate_storage(nse_instruments: int, mcx_instruments: int, days: int, bytes_per_row: int = 130) -> dict:
    """ESTIMATE (assumes a tick in every session second; real feeds are sparser). 1-second candles only."""
    nse_s, mcx_s = 375 * 60, 870 * 60                      # session seconds per day
    rows_day = nse_instruments * nse_s + mcx_instruments * mcx_s
    return {"rows_per_day_max": rows_day, "mb_per_day_max": round(rows_day * bytes_per_row / 1e6, 1),
            "mb_for_days_max": round(rows_day * bytes_per_row * days / 1e6, 1), "assumed_bytes_per_row": bytes_per_row}


class BufferedCandleWriter:
    def __init__(self, path: str | None = None, *, max_queue: int = 50_000, batch_size: int = 500,
                 flush_interval: float = 1.0, max_pending: int = 200_000, on_reject=None):
        self.path = path or config.platform_ticks_db_path()
        self.batch_size, self.flush_interval, self.max_pending = batch_size, flush_interval, max_pending
        self.on_reject = on_reject
        self._q: queue.Queue = queue.Queue(maxsize=max_queue)
        self._pending: list = []                         # batches that failed and await retry
        self._lat: deque = deque(maxlen=500)
        self._stop = threading.Event()
        self._t: threading.Thread | None = None
        self.counters = {"offered": 0, "rejected_full": 0, "written": 0, "write_errors": 0, "lost_over_cap": 0}
        self.last_error: str | None = None
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        return c

    def offer(self, candle) -> bool:
        self.counters["offered"] += 1
        try:
            self._q.put_nowait((time.time(), candle))
            return True
        except queue.Full:
            self.counters["rejected_full"] += 1
            if self.on_reject:
                self.on_reject(candle)
            return False

    # ---------------------------------------------------------------- draining
    def flush_sync(self, max_batches: int = 1000) -> int:
        """Drain the queue (and any retry backlog) in the calling thread; returns rows written."""
        written = 0
        for _ in range(max_batches):
            batch = self._pending.pop(0) if self._pending else self._take()
            if not batch:
                break
            if self._write(batch):
                written += len(batch)
            else:
                self._pending.insert(0, batch)
                break                                       # DB unhealthy: stop, retry on the next cycle
        return written

    def _take(self) -> list:
        out = []
        while len(out) < self.batch_size:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out

    def _write(self, batch: list) -> bool:
        t0 = time.time()
        try:
            rows = [tuple(getattr(c, k) if k != "volume_complete" else int(c.volume_complete) for k in _COLS) for _, c in batch]
            with self._conn() as conn:
                conn.executemany(f"INSERT OR IGNORE INTO candles ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})", rows)
            self.counters["written"] += len(batch)
            self._lat.append(time.time() - t0)
            self.last_error = None
            return True
        except Exception as e:
            self.counters["write_errors"] += 1
            self.last_error = f"{type(e).__name__}: {str(e)[:120]}"
            pending_rows = sum(len(b) for b in self._pending) + len(batch)
            if pending_rows > self.max_pending:
                self.counters["lost_over_cap"] += len(batch)
                if self.on_reject:
                    for _, c in batch:
                        self.on_reject(c)
                return True                                 # reported as lost (counter + callback), not retried forever
            return False

    def write_gaps(self, gaps) -> int:
        n = 0
        with self._conn() as c:
            for g in gaps:
                cur = c.execute("INSERT OR IGNORE INTO gap_events (instrument, after_ts, before_ts, seconds, provider) "
                                "VALUES (?,?,?,?,?)", (g.instrument, g.after_ts, g.before_ts, g.seconds, g.provider))
                n += cur.rowcount or 0
        return n

    # ---------------------------------------------------------------- thread
    def start(self):
        if self._t and self._t.is_alive():
            return
        self._stop.clear()
        self._t = threading.Thread(target=self._run, name="platform-candle-writer", daemon=True)
        self._t.start()

    def stop(self, timeout: float = 5.0):
        self._stop.set()
        if self._t:
            self._t.join(timeout)
        self.flush_sync()

    def _run(self):
        while not self._stop.is_set():
            self.flush_sync()
            self._stop.wait(self.flush_interval)

    # ---------------------------------------------------------------- retention / status
    def purge_older_than(self, cutoff_ts: int, chunk: int = 5000, max_chunks: int = 20) -> int:
        """Bounded indexed delete: at most chunk*max_chunks rows per call, short transactions."""
        deleted = 0
        for _ in range(max_chunks):
            with self._conn() as c:
                cur = c.execute("DELETE FROM candles WHERE rowid IN (SELECT rowid FROM candles WHERE start_ts < ? LIMIT ?)",
                                (cutoff_ts, chunk))
                n = cur.rowcount or 0
            deleted += n
            if n < chunk:
                break
        return deleted

    def status(self) -> dict:
        lat = sorted(self._lat)
        pick = lambda p: (lat[min(len(lat) - 1, int(p * len(lat)))] * 1000) if lat else None  # noqa: E731
        return {**self.counters, "queue_depth": self._q.qsize(), "pending_batches": len(self._pending),
                "write_latency_ms_p50": pick(.5), "write_latency_ms_p95": pick(.95), "write_latency_ms_p99": pick(.99),
                "last_error": self.last_error, "db_path": self.path}
