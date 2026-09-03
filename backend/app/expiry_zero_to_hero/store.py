"""
Forward-capture persistence for the Expiry Zero-to-Hero dataset.

`expiry_z2h_windows` — one JSON blob per (index, expiry, session_date), the raw
02:50-15:40 collector output. Append-only (INSERT OR IGNORE). Its own SQLite
file (data/expiry_z2h.db) so it never touches the trading DB.

Run after each expiry close:
    python -m app.expiry_zero_to_hero collect-store SENSEX 10SEP2026 2026-09-10
    python -m app.expiry_zero_to_hero collect-store NIFTY  08SEP2026 2026-09-08
(A `schedule` routine can call this at ~15:45 IST on each expiry day.)
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "expiry_z2h.db")
DB_PATH = os.path.abspath(os.environ.get("Z2H_DB_PATH", _DEFAULT))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS expiry_z2h_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_ts TEXT NOT NULL,
    index_name TEXT NOT NULL,
    expiry TEXT NOT NULL,
    session_date TEXT NOT NULL,
    window_start TEXT, window_end TEXT,
    atm REAL, step REAL, ref_spot REAL,
    n_strikes INTEGER, index_bars INTEGER, option_bars INTEGER,
    data_notes_json TEXT,
    payload_json TEXT NOT NULL,
    UNIQUE(index_name, expiry, session_date)
);
CREATE TABLE IF NOT EXISTS expiry_z2h_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_ts TEXT NOT NULL,
    index_name TEXT, expiry TEXT, session_date TEXT,
    kind TEXT,               -- replay | oi_leadlag | backtest
    result_json TEXT NOT NULL
);
"""


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def save_window(collected: dict) -> bool:
    """collected = ExpiryDataCollector.collect_window() output. Write-once.
    Returns True if inserted, False if this (index,expiry,date) already exists."""
    m = collected.get("meta", {})
    with _conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO expiry_z2h_windows "
            "(captured_ts,index_name,expiry,session_date,window_start,window_end,"
            " atm,step,ref_spot,n_strikes,index_bars,option_bars,data_notes_json,payload_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), m.get("index"), m.get("expiry"), m.get("session_date"),
             (m.get("window") or [None, None])[0], (m.get("window") or [None, None])[1],
             m.get("atm"), m.get("step"), m.get("ref_spot"), m.get("n_strikes"),
             m.get("index_bars"), m.get("option_bars"),
             json.dumps(m.get("data_notes") or {}),
             json.dumps(collected, default=str)))
        return bool(cur.rowcount)


def save_analysis(index_name, expiry, session_date, kind, result: dict) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO expiry_z2h_analysis (ran_ts,index_name,expiry,session_date,kind,result_json) "
            "VALUES (?,?,?,?,?,?)",
            (_now(), index_name, expiry, session_date, kind, json.dumps(result, default=str)))
        return int(cur.lastrowid)


def list_windows() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id,captured_ts,index_name,expiry,session_date,atm,step,"
            "n_strikes,index_bars,option_bars FROM expiry_z2h_windows "
            "ORDER BY session_date, index_name").fetchall()]


def load_window(index_name, expiry, session_date) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT payload_json FROM expiry_z2h_windows "
                      "WHERE index_name=? AND expiry=? AND session_date=?",
                      (index_name, expiry, session_date)).fetchone()
        return json.loads(r["payload_json"]) if r else None


def dataset_status() -> dict:
    """How close the forward dataset is to a size where coefficients can be fit."""
    ws = list_windows()
    by_index = {}
    for w in ws:
        by_index.setdefault(w["index_name"], []).append(w["expiry"])
    return {
        "db_path": DB_PATH,
        "windows_stored": len(ws),
        "expiry_days_by_index": {k: sorted(set(v)) for k, v in by_index.items()},
        "min_expiry_days_for_calibration": 8,
        "ready_for_coefficient_fit": len({(w["index_name"], w["expiry"]) for w in ws}) >= 8,
        "windows": ws,
    }
