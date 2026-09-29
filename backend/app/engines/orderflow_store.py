"""
Classified-tick persistence for app.engines.orderflow_engine -- a SEPARATE
DB file (own schema, own concerns), not the shared market_candles table.

WHY THIS EXISTS: the live orderflow engine classifies each real trade via
the quote rule (price vs real bid/ask depth from Dhan's WS Full packets),
falling back to the tick rule only when price sits inside the spread. That
classification is only possible with live depth -- Dhan's (or any broker's)
REST historical API returns plain OHLCV with no depth field at all, so the
quote-rule-driven signal can never be backtested retroactively (see the
[[nifty-tick-rule-cvd-nogo]] research memory, which tested the cruder
tick-rule-only fallback instead, for exactly this reason). This module
persists every classified tick GOING FORWARD so that, once enough real
sessions accumulate, the actual live signal -- not a proxy -- can finally be
backtested.

One row per REAL classified trade (dhan_ws.py already deduplicates repeated
depth-only snapshots before a tick reaches the engine, so this is not
flooded with the same trade over and over). RESEARCH DATA ONLY: never read
by any live trading/signal path, purely for future backtesting.
"""
from __future__ import annotations

import os
import sqlite3
import threading

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "dhan_orderflow_ticks.db")
DB_PATH = os.path.abspath(os.environ.get("DHAN_ORDERFLOW_TICKS_DB", _DEFAULT_PATH))

SCHEMA = """
CREATE TABLE IF NOT EXISTS classified_ticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_ts TEXT NOT NULL,
    security_id INTEGER NOT NULL,
    symbol TEXT,
    kind TEXT,
    ltp REAL NOT NULL,
    ltq INTEGER,
    ltt INTEGER NOT NULL,
    side TEXT NOT NULL,
    classification_method TEXT NOT NULL,
    best_bid REAL,
    best_ask REAL,
    cvd INTEGER
);
CREATE INDEX IF NOT EXISTS ix_classified_ticks_lookup ON classified_ticks(security_id, ltt);
"""

_COLS = ("received_ts", "security_id", "symbol", "kind", "ltp", "ltq", "ltt",
        "side", "classification_method", "best_bid", "best_ask", "cvd")


class TickStore:
    def __init__(self, path: str | None = None):
        self.path = os.path.abspath(path or DB_PATH)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=15)
        c.execute("PRAGMA journal_mode=WAL;")
        return c

    def insert_many(self, rows: list[dict]) -> int:
        """Never raises on a malformed row -- skips it (persistence is a
        research nice-to-have, never allowed to crash the live feed)."""
        if not rows:
            return 0
        values = []
        for r in rows:
            try:
                values.append(tuple(r.get(k) for k in _COLS))
            except Exception:
                continue
        if not values:
            return 0
        with self._lock, self._conn() as c:
            cur = c.executemany(
                f"INSERT INTO classified_ticks ({','.join(_COLS)}) "
                f"VALUES ({','.join('?' * len(_COLS))})", values)
            c.commit()
            return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    def count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM classified_ticks").fetchone()[0]

    def close(self):
        pass  # each call opens/closes its own short-lived connection; nothing to hold open
