"""data/nifty_direction.db — versioned migrations (PRAGMA user_version). Short transactions; append-only history tables."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("NIFTY_DIR_DB") or os.path.abspath(os.path.join(HERE, "..", "..", "data", "nifty_direction.db"))
MIGRATIONS = [   # index = target version - 1; never edit an applied entry, append a new one
    """CREATE TABLE signals (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, data_kind TEXT NOT NULL, side TEXT, grade TEXT, total REAL, actionable INTEGER, reason TEXT, bar_t REAL, detail TEXT);
       CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, data_kind TEXT NOT NULL, signal_id INTEGER, inputs TEXT NOT NULL);
       CREATE TABLE health_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, detail TEXT);
       CREATE TABLE config_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, name TEXT NOT NULL, config TEXT NOT NULL);""",
    """CREATE TABLE paper_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id INTEGER, side TEXT NOT NULL, qty INTEGER NOT NULL, entry_ts REAL NOT NULL, entry_px REAL NOT NULL, stop_px REAL, target_px REAL,
       exit_ts REAL, exit_px REAL, exit_reason TEXT, gross REAL, cost REAL, net REAL, data_kind TEXT NOT NULL);
       CREATE INDEX idx_signals_ts ON signals(ts); CREATE INDEX idx_paper_open ON paper_trades(exit_ts);""",
]


def migrate(c: sqlite3.Connection) -> int:
    v = c.execute("PRAGMA user_version").fetchone()[0]
    for i in range(v, len(MIGRATIONS)):
        c.executescript("BEGIN;" + MIGRATIONS[i] + f"PRAGMA user_version={i + 1};COMMIT;")
    return c.execute("PRAGMA user_version").fetchone()[0]


@contextmanager
def conn(path: str | None = None):
    c = sqlite3.connect(path or DB, timeout=15); c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA busy_timeout=15000"); c.execute("PRAGMA journal_mode=WAL"); migrate(c); yield c; c.commit()
    finally:
        c.close()


def add_signal(score: dict, entry: dict | None, inputs: dict, data_kind: str, ts: float | None = None, path=None) -> int:
    ts = ts or time.time()
    with conn(path) as c:
        sid = c.execute("INSERT INTO signals (ts,data_kind,side,grade,total,actionable,reason,bar_t,detail) VALUES (?,?,?,?,?,?,?,?,?)",
                        (ts, data_kind, score.get("side"), score.get("grade"), score.get("total"), int(bool(score.get("actionable"))), score.get("reason"), (entry or {}).get("bar_t"), json.dumps(score, default=str))).lastrowid
        c.execute("INSERT INTO snapshots (ts,data_kind,signal_id,inputs) VALUES (?,?,?,?)", (ts, data_kind, sid, json.dumps(inputs, default=str)))
        return sid


def health(kind: str, detail, path=None, ts=None):
    with conn(path) as c: c.execute("INSERT INTO health_events (ts,kind,detail) VALUES (?,?,?)", (ts or time.time(), kind, json.dumps(detail, default=str)))


def save_config(name: str, cfg: dict, path=None):
    with conn(path) as c: c.execute("INSERT INTO config_versions (ts,name,config) VALUES (?,?,?)", (time.time(), name, json.dumps(cfg)))


def latest_config(name: str, path=None) -> dict | None:
    with conn(path) as c:
        r = c.execute("SELECT config FROM config_versions WHERE name=? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
    return json.loads(r["config"]) if r else None


def rows(table: str, limit: int = 100, path=None) -> list[dict]:
    assert table in ("signals", "health_events", "paper_trades", "config_versions", "snapshots")
    with conn(path) as c: return [dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()]
