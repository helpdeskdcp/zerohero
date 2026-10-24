"""data/chartink.db -- alerts, (alert, symbol) pairs and price marks. Short transactions only (the live price capture must never wait on this file)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("CHARTINK_DB") or os.path.abspath(os.path.join(HERE, "..", "..", "data", "chartink.db"))
SCANS_FILE = os.environ.get("CHARTINK_SCANS") or os.path.abspath(os.path.join(HERE, "..", "..", "data", "chartink", "scans.json"))
HORIZONS = [0, 5, 15, 30, 60, "EOD"]                      # 0 = reference price; minutes; EOD = 15:25 IST
_lock = threading.Lock()
SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, received_ts REAL NOT NULL, received_iso TEXT NOT NULL, session_date TEXT NOT NULL, scan_name TEXT NOT NULL, scan_url TEXT, alert_name TEXT,
  triggered_at_text TEXT, n_symbols INTEGER, raw TEXT);
CREATE TABLE IF NOT EXISTS pairs (id INTEGER PRIMARY KEY AUTOINCREMENT, alert_id INTEGER NOT NULL, symbol TEXT NOT NULL, trigger_price REAL, token TEXT, status TEXT NOT NULL DEFAULT 'PENDING');
CREATE TABLE IF NOT EXISTS marks (pair_id INTEGER NOT NULL, horizon TEXT NOT NULL, due_ts REAL NOT NULL, ts REAL, price REAL, nifty REAL, status TEXT NOT NULL DEFAULT 'PENDING', PRIMARY KEY (pair_id, horizon));
CREATE INDEX IF NOT EXISTS ix_pairs_alert ON pairs(alert_id);
CREATE INDEX IF NOT EXISTS ix_marks_due ON marks(status, due_ts);
CREATE INDEX IF NOT EXISTS ix_alerts_scan ON alerts(scan_name, session_date);
"""


@contextmanager
def conn(path: str | None = None):
    c = sqlite3.connect(path or DB, timeout=15); c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=15000"); c.executescript(SCHEMA); yield c; c.commit()
    finally:
        c.close()


def eod_ts(received_ts: float) -> float:
    d = datetime.fromtimestamp(received_ts, IST); return d.replace(hour=15, minute=25, second=0, microsecond=0).timestamp()


def add_alert(parsed: dict, raw: bytes, now: float | None = None, path: str | None = None) -> dict:
    now = now or time.time(); d = datetime.fromtimestamp(now, IST); syms = parsed["symbols"]
    with _lock, conn(path) as c:
        cur = c.execute("INSERT INTO alerts (received_ts, received_iso, session_date, scan_name, scan_url, alert_name, triggered_at_text, n_symbols, raw) VALUES (?,?,?,?,?,?,?,?,?)",
                        (now, d.isoformat(timespec="seconds"), d.strftime("%Y-%m-%d"), parsed["scan_name"], parsed.get("scan_url"), parsed.get("alert_name"), parsed.get("triggered_at_text"), len(syms), raw[:20000].decode("utf-8", "replace")))
        aid = cur.lastrowid; eod = eod_ts(now)
        for s in syms:
            pc = c.execute("INSERT INTO pairs (alert_id, symbol, trigger_price) VALUES (?,?,?)", (aid, s["symbol"], s.get("trigger_price"))).lastrowid
            for h in HORIZONS:
                due = now if h == 0 else (eod if h == "EOD" else now + int(h) * 60)
                st = "PENDING" if due <= eod + 1 else "SKIPPED_CLOSE"
                c.execute("INSERT INTO marks (pair_id, horizon, due_ts, status) VALUES (?,?,?,?)", (pc, str(h), due, st))
    return {"alert_id": aid, "n_symbols": len(syms), "session_date": d.strftime("%Y-%m-%d")}


def load_scan_sides() -> dict:
    try: return json.load(open(SCANS_FILE))
    except Exception: return {}


def set_scan_side(scan_name: str, side: str) -> dict:
    side = side.upper()
    if side not in ("LONG", "SHORT"): raise ValueError("side must be LONG or SHORT")
    d = load_scan_sides()
    with conn() as c:
        if c.execute("SELECT 1 FROM marks m JOIN pairs p ON p.id=m.pair_id JOIN alerts a ON a.id=p.alert_id WHERE a.scan_name=? AND m.status='OK' AND m.horizon!='0' LIMIT 1", (scan_name,)).fetchone():
            raise ValueError("side is locked: this scan already has evaluated marks (spec: the side may not change after results exist)")
    d[scan_name] = {"side": side, "set": time.strftime("%F %T")}
    os.makedirs(os.path.dirname(SCANS_FILE), exist_ok=True); json.dump(d, open(SCANS_FILE, "w"), indent=1); return d[scan_name]
