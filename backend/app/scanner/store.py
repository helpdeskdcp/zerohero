"""data/scanner.db -- raw Upstox daily rows (cache), scan hits. Short transactions."""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("SCANNER_DB") or os.path.abspath(os.path.join(HERE, "..", "..", "data", "scanner.db"))
SCHEMA = """
CREATE TABLE IF NOT EXISTS upstox_daily (inst TEXT NOT NULL, ikey TEXT NOT NULL, date TEXT NOT NULL, o REAL, h REAL, l REAL, c REAL, v REAL, PRIMARY KEY (inst, ikey, date));
CREATE TABLE IF NOT EXISTS hits (id INTEGER PRIMARY KEY AUTOINCREMENT, inst TEXT NOT NULL, scan TEXT NOT NULL, side TEXT NOT NULL, signal_date TEXT NOT NULL, close REAL, created_ts REAL NOT NULL, UNIQUE (inst, scan, signal_date));
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, group_name TEXT, info TEXT);
"""


@contextmanager
def conn(path: str | None = None):
    c = sqlite3.connect(path or DB, timeout=15); c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=15000"); c.executescript(SCHEMA); yield c; c.commit()
    finally:
        c.close()


def add_hit(inst, scan, side, signal_date, close, path=None) -> bool:
    with conn(path) as c:
        return c.execute("INSERT OR IGNORE INTO hits (inst, scan, side, signal_date, close, created_ts) VALUES (?,?,?,?,?,?)", (inst, scan, side, signal_date, float(close), time.time())).rowcount == 1
