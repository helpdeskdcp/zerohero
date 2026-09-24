"""Shadow log for computed decoupling classifications. Own db, additive only
-- nothing else reads or writes this file, and this module never touches
market_history.db or chanakya.db."""
from __future__ import annotations

import sqlite3
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = _ROOT / "data" / "premium_decoupling.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decoupling_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    underlying TEXT NOT NULL,
    window_sec INTEGER NOT NULL,
    window_start_ts TEXT NOT NULL,
    window_end_ts TEXT NOT NULL,
    spot_open REAL, spot_close REAL, spot_delta REAL, spot_delta_pct REAL,
    expiry TEXT,
    ce_strike REAL, ce_open REAL, ce_close REAL, ce_delta REAL, ce_delta_pct REAL,
    pe_strike REAL, pe_open REAL, pe_close REAL, pe_delta REAL, pe_delta_pct REAL,
    classification TEXT NOT NULL,
    explanation TEXT,
    logged_ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(underlying, window_sec, window_end_ts)
);
CREATE INDEX IF NOT EXISTS idx_decoupling_underlying_ts
    ON decoupling_log(underlying, window_end_ts);
"""

_COLS = ("underlying", "window_sec", "window_start_ts", "window_end_ts",
          "spot_open", "spot_close", "spot_delta", "spot_delta_pct", "expiry",
          "ce_strike", "ce_open", "ce_close", "ce_delta", "ce_delta_pct",
          "pe_strike", "pe_open", "pe_close", "pe_delta", "pe_delta_pct",
          "classification", "explanation")


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    return conn


def log_result(result: dict, db_path: Path | str | None = None) -> int | None:
    """Idempotent: re-logging the same (underlying, window_sec, window_end_ts)
    is a silent no-op, so a cron re-run never duplicates a row."""
    if result.get("classification") in ("INSUFFICIENT_DATA",):
        return None  # nothing informative to keep
    conn = _connect(db_path)
    try:
        placeholders = ",".join("?" for _ in _COLS)
        cur = conn.execute(
            f"INSERT OR IGNORE INTO decoupling_log ({','.join(_COLS)}) VALUES ({placeholders})",
            tuple(result.get(c) for c in _COLS))
        conn.commit()
        return cur.lastrowid or None
    finally:
        conn.close()


def recent(underlying: str, limit: int = 50, db_path: Path | str | None = None) -> list[dict]:
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM decoupling_log WHERE underlying=? ORDER BY window_end_ts DESC LIMIT ?",
            (underlying.upper(), limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def summary_counts(underlying: str, since_ts: str | None = None,
                    db_path: Path | str | None = None) -> dict:
    conn = _connect(db_path)
    try:
        q = "SELECT classification, COUNT(*) FROM decoupling_log WHERE underlying=?"
        params: tuple = (underlying.upper(),)
        if since_ts:
            q += " AND window_end_ts >= ?"
            params = (underlying.upper(), since_ts)
        q += " GROUP BY classification ORDER BY COUNT(*) DESC"
        return dict(conn.execute(q, params).fetchall())
    finally:
        conn.close()
