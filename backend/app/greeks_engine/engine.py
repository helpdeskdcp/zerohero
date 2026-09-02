"""
GreeksEngine — derives exposure metrics from histcap-captured broker Greeks + OI.

Reads `option_greeks` (broker) + `quote_snapshots` (OI, underlying price) from the
shared `market_history.db`; writes derived snapshots to `greek_exposure`
(append-only). No broker fetch, no fabrication, no trading-logic touch.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

from . import schema
from .compute import build_snapshot
from .model import EXPOSURE_COLS, RUN_COLS, SOURCE

try:
    from ..histcap.store import DB_PATH as _HIST_DB
except Exception:                                    # pragma: no cover
    _HIST_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "data", "market_history.db")

STALE_SEC = float(os.environ.get("CHANAKYA_GREEKS_STALE_SEC", "90"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class GreeksEngine:
    def __init__(self, db_path: str | None = None):
        self.path = os.path.abspath(db_path or _HIST_DB)
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with self._conn() as c:
            schema.init(c)

    def _conn(self):
        c = sqlite3.connect(self.path, check_same_thread=False, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL;")
        return c

    # ------------------------------------------------------------------ read helpers
    def _expiries(self, conn, underlying: str, session_date_ist: str | None) -> list[str]:
        q = ("SELECT DISTINCT expiry FROM option_greeks WHERE underlying=? AND broker_status='OK'"
             + (" AND session_date_ist=?" if session_date_ist else ""))
        p = [underlying.upper()] + ([session_date_ist] if session_date_ist else [])
        return [r["expiry"] for r in conn.execute(q, p).fetchall()]

    def _target_ts(self, conn, underlying: str, expiry: str, as_of: str | None) -> str | None:
        if as_of:
            r = conn.execute(
                "SELECT MAX(received_ts) t FROM option_greeks WHERE underlying=? AND expiry=? "
                "AND broker_status='OK' AND received_ts<=?", (underlying.upper(), expiry, as_of)).fetchone()
        else:
            r = conn.execute(
                "SELECT MAX(received_ts) t FROM option_greeks WHERE underlying=? AND expiry=? "
                "AND broker_status='OK'", (underlying.upper(), expiry)).fetchone()
        return r["t"] if r and r["t"] else None

    def _greek_rows(self, conn, underlying: str, expiry: str, ts: str) -> list[dict]:
        return [dict(r) for r in conn.execute(
            "SELECT strike, option_type, delta, gamma, theta, vega, iv, server_ts "
            "FROM option_greeks WHERE underlying=? AND expiry=? AND broker_status='OK' "
            "AND received_ts=?", (underlying.upper(), expiry, ts)).fetchall()]

    def _oi_map(self, conn, underlying: str, expiry: str, ts: str) -> tuple[dict, float | None, float | None]:
        """{(strike, side): oi} at exactly `ts` (same capture cycle), plus the
        min/max option strike captured (for the coverage band)."""
        rows = conn.execute(
            "SELECT strike, option_type, oi FROM quote_snapshots WHERE symbol=? AND expiry=? "
            "AND kind='OPTION' AND received_ts=?", (underlying.upper(), expiry, ts)).fetchall()
        m = {(float(r["strike"]), str(r["option_type"]).upper()): r["oi"]
             for r in rows if r["strike"] is not None}
        strikes = [float(r["strike"]) for r in rows if r["strike"] is not None]
        return m, (min(strikes) if strikes else None), (max(strikes) if strikes else None)

    def _underlying_price(self, conn, underlying: str, ts: str) -> tuple[float | None, str | None]:
        for kind in ("FUTURE", "INDEX"):
            r = conn.execute(
                "SELECT ltp FROM quote_snapshots WHERE symbol=? AND kind=? "
                "ORDER BY ABS(julianday(received_ts)-julianday(?)) LIMIT 1",
                (underlying.upper(), kind, ts)).fetchone()
            if r and r["ltp"] is not None:
                return float(r["ltp"]), f"ANGELONE_QUOTE:{kind}"
        return None, None

    # ------------------------------------------------------------------ run
    def run_once(self, underlying: str = "NIFTY", expiries: list[str] | None = None,
                 as_of: str | None = None, mode: str = "ONCE",
                 session_date_ist: str | None = None) -> dict:
        with self._lock, self._conn() as conn:
            rid = conn.execute(
                "INSERT INTO greek_engine_runs(started_ts, mode, underlying, expiries_json) "
                "VALUES (?,?,?,?)", (_now(), mode, underlying.upper(),
                                     json.dumps(expiries or []))).lastrowid
            conn.commit()

            exps = expiries or self._expiries(conn, underlying, session_date_ist)
            written, quality, errors = 0, {}, []
            for exp in exps:
                try:
                    ts = self._target_ts(conn, underlying, exp, as_of)
                    if not ts:
                        errors.append({"expiry": exp, "reason": "no OK greek rows"})
                        quality[exp] = "NO_DATA"
                        continue
                    greeks = self._greek_rows(conn, underlying, exp, ts)
                    oi_map, s_lo, s_hi = self._oi_map(conn, underlying, exp, ts)
                    px, px_src = self._underlying_price(conn, underlying, ts)

                    band = [g for g in greeks
                            if s_lo is not None and s_hi is not None
                            and s_lo - 1e-6 <= float(g["strike"]) <= s_hi + 1e-6]
                    expected = len(band) or len(greeks)
                    for g in greeks:
                        g["oi"] = oi_map.get((float(g["strike"]), str(g["option_type"]).upper()))
                    srv = next((g.get("server_ts") for g in greeks if g.get("server_ts")), None)

                    snap = build_snapshot(
                        greeks, underlying=underlying, expiry=exp,
                        underlying_price=px, underlying_price_src=px_src,
                        as_of_ts=srv or ts, expected_pairs=expected,
                        stale_sec_threshold=STALE_SEC)
                    snap["run_id"] = rid
                    written += self._write(conn, snap)
                    quality[exp] = snap["quality"]
                except Exception as e:
                    errors.append({"expiry": exp, "error": f"{type(e).__name__}: {e}"})

            conn.execute(
                "UPDATE greek_engine_runs SET ended_ts=?, snapshots_written=?, quality_json=?, "
                "errors_json=?, notes=? WHERE id=?",
                (_now(), written, json.dumps(quality), json.dumps(errors),
                 f"expiries={exps}", rid))
            conn.commit()
            return {"run_id": rid, "underlying": underlying.upper(), "expiries": exps,
                    "snapshots_written": written, "quality": quality, "errors": errors}

    def _write(self, conn, snap: dict) -> int:
        row = {**snap, "per_strike_json": json.dumps(snap.get("per_strike") or [], default=str),
               "source": SOURCE}
        cur = conn.execute(
            f"INSERT OR IGNORE INTO greek_exposure ({','.join(EXPOSURE_COLS)}) "
            f"VALUES ({','.join('?' * len(EXPOSURE_COLS))})",
            tuple(row.get(c) for c in EXPOSURE_COLS))
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    # ------------------------------------------------------------------ read API
    def latest(self, underlying: str = "NIFTY", expiry: str | None = None) -> dict | None:
        with self._conn() as c:
            q = "SELECT * FROM greek_exposure WHERE underlying=?"
            p = [underlying.upper()]
            if expiry:
                q += " AND expiry=?"; p.append(expiry)
            q += " ORDER BY as_of_ts DESC LIMIT 1"
            r = c.execute(q, p).fetchone()
            return _decode(dict(r)) if r else None

    def history(self, underlying: str = "NIFTY", *, expiry: str | None = None,
                as_of: str | None = None, since: str | None = None,
                limit: int = 2000) -> list[dict]:
        """Look-ahead-safe: rows with as_of_ts <= as_of (if given), oldest first."""
        clauses, p = ["underlying=?"], [underlying.upper()]
        if expiry:
            clauses.append("expiry=?"); p.append(expiry)
        if as_of:
            clauses.append("as_of_ts <= ?"); p.append(as_of)
        if since:
            clauses.append("as_of_ts >= ?"); p.append(since)
        p.append(limit)
        with self._conn() as c:
            return [_decode(dict(r)) for r in c.execute(
                f"SELECT * FROM greek_exposure WHERE {' AND '.join(clauses)} "
                f"ORDER BY as_of_ts ASC LIMIT ?", p).fetchall()]

    def runs(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM greek_engine_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def status(self) -> dict:
        with self._conn() as c:
            def one(sql):
                r = c.execute(sql).fetchone()
                return r[0] if r else None
            return {
                "db_path": self.path,
                "exposure_snapshots": one("SELECT COUNT(*) FROM greek_exposure"),
                "by_quality": {r["quality"]: r["n"] for r in c.execute(
                    "SELECT quality, COUNT(*) n FROM greek_exposure GROUP BY quality").fetchall()},
                "span": [one("SELECT MIN(as_of_ts) FROM greek_exposure"),
                         one("SELECT MAX(as_of_ts) FROM greek_exposure")],
                "runs": one("SELECT COUNT(*) FROM greek_engine_runs"),
                "last_run": (self.runs(1) or [None])[0],
                "stale_sec_threshold": STALE_SEC,
            }


def _decode(row: dict) -> dict:
    if isinstance(row.get("per_strike_json"), str):
        try:
            row["per_strike"] = json.loads(row["per_strike_json"])
        except (ValueError, TypeError):
            row["per_strike"] = []
    return row


_singleton: GreeksEngine | None = None


def greeks_engine() -> GreeksEngine:
    global _singleton
    if _singleton is None:
        _singleton = GreeksEngine()
    return _singleton
