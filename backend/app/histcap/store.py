"""
`market_history.db` access layer — append-only writes, look-ahead-safe reads.

Writes use INSERT OR IGNORE on the UNIQUE natural keys, so re-capturing a window
is a no-op. Reads filter on the *exchange* timestamp (fallback: received, flagged)
so a backtest never sees a bar before the exchange published it.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

from . import schema
from .integrity import candle_check, quote_check, greek_check

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "market_history.db")
DB_PATH = os.path.abspath(os.environ.get("CHANAKYA_HIST_DB_PATH", _DEFAULT_PATH))

_CANDLE_COLS = ("received_ts", "instrument_key", "symbol", "kind", "exchange", "token",
                "expiry", "strike", "option_type", "tf", "bar_start", "session_date_ist",
                "o", "h", "l", "c", "v", "oi", "oi_change", "source", "raw_id", "flags", "run_id")
_QUOTE_COLS = ("received_ts", "server_ts", "exch_ts", "snap_key", "instrument_key", "symbol",
               "kind", "exchange", "token", "expiry", "strike", "option_type", "session_date_ist",
               "ltp", "open", "high", "low", "close", "volume", "oi", "oi_change", "avg_price",
               "last_trade_qty", "bid", "ask", "bid_qty", "ask_qty", "tot_buy_qty", "tot_sell_qty",
               "depth_json", "net_change", "pct_change", "lower_circuit", "upper_circuit",
               "week52_high", "week52_low", "basis", "quote_status", "source", "raw_id", "flags", "run_id")
_GREEK_COLS = ("received_ts", "server_ts", "snap_key", "underlying", "expiry", "strike",
               "option_type", "session_date_ist", "delta", "gamma", "theta", "vega", "iv",
               "iv_pct", "trade_volume", "broker_status", "source", "raw_id", "flags", "run_id")


class HistStore:
    def __init__(self, path: str | None = None):
        self.path = os.path.abspath(path or DB_PATH)
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with self._conn() as c:
            schema.init(c)

    def _conn(self):
        c = sqlite3.connect(self.path, check_same_thread=False, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("PRAGMA foreign_keys=ON;")
        return c

    # -------------------------------------------------- runs
    def start_run(self, mode: str, market_state: dict, auth_ok: bool) -> int:
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO capture_runs(started_ts, mode, market_state, auth_ok) VALUES (?,?,?,?)",
                (_now(), mode, json.dumps(market_state), 1 if auth_ok else 0))
            c.commit()
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, *, instruments_n=0, counts=None, integrity=None,
                   errors=None, notes: str = "") -> None:
        counts = counts or {}
        with self._lock, self._conn() as c:
            c.execute(
                "UPDATE capture_runs SET ended_ts=?, instruments_n=?, candles_written=?, "
                "quotes_written=?, greeks_written=?, raw_written=?, integrity_json=?, "
                "errors_json=?, notes=? WHERE id=?",
                (_now(), instruments_n, counts.get("candles", 0), counts.get("quotes", 0),
                 counts.get("greeks", 0), counts.get("raw", 0),
                 json.dumps(integrity or {}), json.dumps(errors or []), notes, run_id))
            c.commit()

    # -------------------------------------------------- raw payloads
    def put_raw(self, conn, *, endpoint: str, request: dict | None, http_status,
                status: str, payload, server_ts: str | None, run_id: int) -> int:
        body = json.dumps(payload, default=str, sort_keys=True)
        sha = hashlib.sha256((endpoint + "|" + body).encode("utf-8")).hexdigest()
        row = conn.execute("SELECT id FROM raw_responses WHERE sha256=?", (sha,)).fetchone()
        if row:
            return int(row["id"])
        gz = base64.b64encode(gzip.compress(body.encode("utf-8"))).decode("ascii")
        cur = conn.execute(
            "INSERT OR IGNORE INTO raw_responses"
            "(received_ts, server_ts, endpoint, request_json, http_status, status, sha256, gzip_b64, run_id)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (_now(), server_ts, endpoint, json.dumps(request or {}), http_status, status, sha, gz, run_id))
        if cur.lastrowid:
            return int(cur.lastrowid)
        got = conn.execute("SELECT id FROM raw_responses WHERE sha256=?", (sha,)).fetchone()
        return int(got["id"]) if got else 0

    # -------------------------------------------------- normalized writes (append-only)
    def write_candles(self, conn, rows: list[dict], run_id: int, integ: dict) -> int:
        n = 0
        for r in rows:
            hard_ok, flags = candle_check(r.get("o"), r.get("h"), r.get("l"),
                                          r.get("c"), r.get("v"), r.get("oi"))
            if not hard_ok:
                integ.setdefault("rejected", []).append(
                    {"table": "market_candles", "key": f'{r["instrument_key"]}|{r["tf"]}|{r["bar_start"]}',
                     "reason": flags})
                continue
            if flags:
                integ["flagged"] = integ.get("flagged", 0) + 1
            r = {**r, "flags": ",".join(flags) or None, "run_id": run_id}
            cur = conn.execute(
                f"INSERT OR IGNORE INTO market_candles ({','.join(_CANDLE_COLS)}) "
                f"VALUES ({','.join('?' * len(_CANDLE_COLS))})",
                tuple(r.get(k) for k in _CANDLE_COLS))
            n += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        return n

    def write_quotes(self, conn, rows: list[dict], run_id: int, integ: dict) -> int:
        n = 0
        for r in rows:
            flags = quote_check(r.get("ltp"), r.get("bid"), r.get("ask"),
                                r.get("oi"), r.get("oi_change"))
            if flags:
                integ["flagged"] = integ.get("flagged", 0) + 1
            r = {**r, "depth_json": json.dumps(r.get("depth_json")) if r.get("depth_json") else None,
                 "flags": ",".join(flags) or None, "run_id": run_id}
            cur = conn.execute(
                f"INSERT OR IGNORE INTO quote_snapshots ({','.join(_QUOTE_COLS)}) "
                f"VALUES ({','.join('?' * len(_QUOTE_COLS))})",
                tuple(r.get(k) for k in _QUOTE_COLS))
            n += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        return n

    def write_greeks(self, conn, rows: list[dict], run_id: int, integ: dict) -> int:
        n = 0
        for r in rows:
            flags = greek_check(r.get("delta"), r.get("gamma"), r.get("theta"),
                                r.get("vega"), r.get("iv"))
            if flags:
                integ["flagged"] = integ.get("flagged", 0) + 1
            r = {**r, "flags": ",".join(flags) or None, "run_id": run_id}
            cur = conn.execute(
                f"INSERT OR IGNORE INTO option_greeks ({','.join(_GREEK_COLS)}) "
                f"VALUES ({','.join('?' * len(_GREEK_COLS))})",
                tuple(r.get(k) for k in _GREEK_COLS))
            n += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        return n

    def transaction(self):
        return _Txn(self)

    # -------------------------------------------------- look-ahead-safe reads
    def get_candles(self, symbol: str, tf: str, *, as_of: str | None = None,
                    kind: str = "FUTURE", limit: int = 5000) -> list[dict]:
        q = ("SELECT * FROM market_candles WHERE symbol=? AND tf=? AND kind=? "
             + ("AND bar_start <= ? " if as_of else "")
             + "ORDER BY bar_start ASC LIMIT ?")
        p = [symbol.upper(), tf, kind] + ([as_of] if as_of else []) + [limit]
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, p).fetchall()]

    def get_quotes(self, symbol: str, *, as_of: str | None = None, kind: str | None = None,
                   expiry: str | None = None, strike: float | None = None,
                   option_type: str | None = None, limit: int = 5000) -> list[dict]:
        clauses, p = ["symbol=?"], [symbol.upper()]
        for col, val in (("kind", kind), ("expiry", expiry), ("option_type", option_type)):
            if val is not None:
                clauses.append(f"{col}=?"); p.append(val)
        if strike is not None:
            clauses.append("strike=?"); p.append(float(strike))
        if as_of:
            clauses.append("COALESCE(exch_ts, received_ts) <= ?"); p.append(as_of)
        p.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                f"SELECT * FROM quote_snapshots WHERE {' AND '.join(clauses)} "
                f"ORDER BY COALESCE(exch_ts, received_ts) ASC LIMIT ?", p).fetchall()]

    def get_greeks(self, underlying: str, *, as_of: str | None = None, expiry: str | None = None,
                   strike: float | None = None, option_type: str | None = None,
                   limit: int = 5000) -> list[dict]:
        clauses, p = ["underlying=?", "broker_status='OK'"], [underlying.upper()]
        for col, val in (("expiry", expiry), ("option_type", option_type)):
            if val is not None:
                clauses.append(f"{col}=?"); p.append(val)
        if strike is not None:
            clauses.append("strike=?"); p.append(float(strike))
        if as_of:
            clauses.append("COALESCE(server_ts, received_ts) <= ?"); p.append(as_of)
        p.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                f"SELECT * FROM option_greeks WHERE {' AND '.join(clauses)} "
                f"ORDER BY COALESCE(server_ts, received_ts) ASC LIMIT ?", p).fetchall()]

    def runs(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM capture_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def summary(self) -> dict:
        with self._conn() as c:
            def one(sql, *a):
                r = c.execute(sql, a).fetchone()
                return r[0] if r else None
            return {
                "db_path": self.path,
                "db_bytes": os.path.getsize(self.path) if os.path.exists(self.path) else 0,
                "candles": one("SELECT COUNT(*) FROM market_candles"),
                "quotes": one("SELECT COUNT(*) FROM quote_snapshots"),
                "greeks_ok": one("SELECT COUNT(*) FROM option_greeks WHERE broker_status='OK'"),
                "greeks_rows": one("SELECT COUNT(*) FROM option_greeks"),
                "raw": one("SELECT COUNT(*) FROM raw_responses"),
                "runs": one("SELECT COUNT(*) FROM capture_runs"),
                "candle_span": [one("SELECT MIN(bar_start) FROM market_candles"),
                                one("SELECT MAX(bar_start) FROM market_candles")],
                "quote_span": [one("SELECT MIN(COALESCE(exch_ts,received_ts)) FROM quote_snapshots"),
                               one("SELECT MAX(COALESCE(exch_ts,received_ts)) FROM quote_snapshots")],
                "last_run": (self.runs(1) or [None])[0],
            }


class _Txn:
    """One SQLite transaction for a whole capture cycle (batch insert)."""
    def __init__(self, store: HistStore):
        self.store = store

    def __enter__(self):
        self.store._lock.acquire()
        self.conn = self.store._conn()
        self.conn.execute("BEGIN")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()
            self.store._lock.release()
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_singleton: HistStore | None = None


def hist_store() -> HistStore:
    global _singleton
    if _singleton is None:
        _singleton = HistStore()
    return _singleton
