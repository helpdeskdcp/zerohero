"""
Append-only SQLite sink for Angel SnapQuote (mode-3) capture.

SEPARATE research DB (default backend/data/l2_capture.db) -- does NOT touch
market_history.db, its schema, or the histcap capture path. Rows are
INSERT-OR-IGNORE only; nothing is ever UPDATEd or DELETEd. Raw binary frames are
preserved gzip+sha256 exactly like app/histcap/store.put_raw. No field is
fabricated: a value the packet does not carry is stored NULL.

See backend/ORDERFLOW_STAGE9_L2_RESEARCH.md section 6b (route (a)) and
backend/L2_SNAPQUOTE_CAPTURE.md.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "l2_capture.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS capture_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_ts  TEXT NOT NULL,
    ended_ts    TEXT,
    reason      TEXT,
    tokens_json TEXT,
    n_frames    INTEGER DEFAULT 0,
    n_ticks     INTEGER DEFAULT 0,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS raw_frames (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    received_ts TEXT NOT NULL,
    sha256      TEXT NOT NULL UNIQUE,
    gzip_b64    TEXT NOT NULL,
    n_bytes     INTEGER,
    n_packets   INTEGER,
    run_id      INTEGER
);

CREATE TABLE IF NOT EXISTS snapquote_ticks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    received_ts   TEXT NOT NULL,               -- our wall clock, UTC ISO
    exch_ts_ms    INTEGER,                     -- Angel exchange timestamp (ms)
    exch_ts       TEXT,                        -- derived ISO of exch_ts_ms
    session_date_ist TEXT NOT NULL,
    seq           INTEGER,
    token         TEXT NOT NULL,
    exchange_type INTEGER,
    symbol        TEXT,                        -- friendly, if the worker resolved it
    kind          TEXT,                        -- FUTURE | INDEX | OPTION | NULL
    ltp REAL, ltq REAL, atp REAL, volume REAL,
    tot_buy_qty REAL, tot_sell_qty REAL,
    open REAL, high REAL, low REAL, close REAL,
    oi REAL, oi_change_pct REAL, ltt_epoch INTEGER,
    bid REAL, ask REAL, bid_qty REAL, ask_qty REAL,
    depth_json    TEXT,                        -- {"buy":[{price,quantity,orders}x5],"sell":[...]}
    upper_circuit REAL, lower_circuit REAL, week52_high REAL, week52_low REAL,
    raw_id        INTEGER REFERENCES raw_frames(id),
    run_id        INTEGER,
    snap_key      TEXT NOT NULL,               -- token:seq:exch_ts_ms  -> dedup
    UNIQUE(snap_key)
);
CREATE INDEX IF NOT EXISTS ix_sq_token_ts ON snapquote_ticks(token, exch_ts_ms);
CREATE INDEX IF NOT EXISTS ix_sq_session  ON snapquote_ticks(session_date_ist);
"""

_IST_OFFSET = 5.5 * 3600


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_date_ist(exch_ts_ms: int | None, received_ts: str) -> str:
    if exch_ts_ms and exch_ts_ms > 0:
        t = datetime.fromtimestamp(exch_ts_ms / 1000 + _IST_OFFSET, tz=timezone.utc)
        return t.date().isoformat()
    try:
        t = datetime.fromisoformat(received_ts.replace("Z", "+00:00"))
        return datetime.fromtimestamp(t.timestamp() + _IST_OFFSET, tz=timezone.utc).date().isoformat()
    except Exception:
        return datetime.now(timezone.utc).date().isoformat()


class L2Store:
    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("L2_CAPTURE_DB") or _DEFAULT_PATH
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.Lock()
        self._con = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=NORMAL")
        self._con.execute("PRAGMA busy_timeout=30000")
        self._con.executescript(_SCHEMA)
        self._con.commit()

    # ---------------- runs ----------------
    def start_run(self, reason: str, tokens: list) -> int:
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO capture_runs(started_ts, reason, tokens_json) VALUES (?,?,?)",
                (_now_utc_iso(), reason, json.dumps(tokens)))
            self._con.commit()
            return cur.lastrowid

    def finish_run(self, run_id: int, *, n_frames: int = 0, n_ticks: int = 0, note: str | None = None):
        if not run_id:
            return
        with self._lock:
            self._con.execute(
                "UPDATE capture_runs SET ended_ts=?, n_frames=?, n_ticks=?, note=? WHERE id=?",
                (_now_utc_iso(), n_frames, n_ticks, note, run_id))
            self._con.commit()

    # ---------------- raw frame preservation ----------------
    def put_raw_frame(self, payload: bytes, *, n_packets: int, run_id: int | None) -> int | None:
        sha = hashlib.sha256(payload).hexdigest()
        gz = base64.b64encode(gzip.compress(payload)).decode("ascii")
        with self._lock:
            row = self._con.execute("SELECT id FROM raw_frames WHERE sha256=?", (sha,)).fetchone()
            if row:
                return row[0]
            cur = self._con.execute(
                "INSERT INTO raw_frames(received_ts, sha256, gzip_b64, n_bytes, n_packets, run_id) "
                "VALUES (?,?,?,?,?,?)",
                (_now_utc_iso(), sha, gz, len(payload), n_packets, run_id))
            self._con.commit()
            return cur.lastrowid

    # ---------------- ticks ----------------
    def insert_ticks(self, recs: list[dict], *, raw_id: int | None, run_id: int | None,
                     meta: dict | None = None) -> int:
        """recs = parse_snapquote() output. meta: {token: {"symbol":..,"kind":..}}.
        Returns the number of NEW rows actually written."""
        meta = meta or {}
        n_before = self._con.total_changes
        rts = _now_utc_iso()
        with self._lock:
            for r in recs:
                ets = r.get("exch_ts_ms") or 0
                exch_iso = (datetime.fromtimestamp(ets / 1000, tz=timezone.utc).isoformat()
                            if ets > 0 else None)
                m = meta.get(str(r.get("token"))) or {}
                snap_key = f"{r.get('token')}:{r.get('seq')}:{ets}"
                self._con.execute(
                    "INSERT OR IGNORE INTO snapquote_ticks("
                    "received_ts, exch_ts_ms, exch_ts, session_date_ist, seq, token, exchange_type, "
                    "symbol, kind, ltp, ltq, atp, volume, tot_buy_qty, tot_sell_qty, "
                    "open, high, low, close, oi, oi_change_pct, ltt_epoch, "
                    "bid, ask, bid_qty, ask_qty, depth_json, "
                    "upper_circuit, lower_circuit, week52_high, week52_low, raw_id, run_id, snap_key) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rts, ets or None, exch_iso, _session_date_ist(ets, rts), r.get("seq"),
                     str(r.get("token")), r.get("exchange_type"), m.get("symbol"), m.get("kind"),
                     r.get("ltp"), r.get("ltq"), r.get("atp"), r.get("volume"),
                     r.get("tot_buy_qty"), r.get("tot_sell_qty"),
                     r.get("open"), r.get("high"), r.get("low"), r.get("close"),
                     r.get("oi"), r.get("oi_change_pct"), r.get("ltt_epoch"),
                     r.get("bid"), r.get("ask"), r.get("bid_qty"), r.get("ask_qty"),
                     json.dumps(r.get("depth") or {}),
                     r.get("upper_circuit"), r.get("lower_circuit"),
                     r.get("week52_high"), r.get("week52_low"),
                     raw_id, run_id, snap_key))
            self._con.commit()
        return self._con.total_changes - n_before

    # ---------------- read / status ----------------
    def summary(self) -> dict:
        with self._lock:
            q = self._con.execute
            t = q("SELECT COUNT(*), MIN(session_date_ist), MAX(session_date_ist), "
                  "COUNT(DISTINCT session_date_ist), COUNT(DISTINCT token) FROM snapquote_ticks").fetchone()
            f = q("SELECT COUNT(*), COALESCE(SUM(n_bytes),0) FROM raw_frames").fetchone()
            per = q("SELECT token, symbol, COUNT(*), MIN(exch_ts), MAX(exch_ts) "
                    "FROM snapquote_ticks GROUP BY token ORDER BY 3 DESC LIMIT 12").fetchall()
        return {
            "db": self.path,
            "ticks": t[0], "sessions": t[3], "first_session": t[1], "last_session": t[2],
            "distinct_tokens": t[4], "raw_frames": f[0], "raw_bytes": f[1],
            "per_token": [
                {"token": r[0], "symbol": r[1], "ticks": r[2], "first": r[3], "last": r[4]}
                for r in per
            ],
        }

    def close(self):
        try:
            self._con.close()
        except Exception:
            pass


# module-level convenience for a probe / one-off
def open_store(path: str | None = None) -> L2Store:
    return L2Store(path)
