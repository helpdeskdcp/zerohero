"""
GreeksEngine — derives exposure metrics from histcap-captured broker Greeks + OI.

Reads `option_greeks` (broker) + `quote_snapshots` (OI, underlying price) from the
shared `market_history.db`; writes derived snapshots to `greek_exposure`
(append-only). No broker fetch, no fabrication, no trading-logic touch.

The output contract is AngelOne-derived: only explicitly attributed AngelOne
Greeks and quotes may contribute. Other/unknown sources remain in histcap but
cannot supply missing inputs or select this engine's capture timestamp.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

from . import schema
from .compute import build_snapshot
from .model import EXPOSURE_COLS, SOURCE

try:
    from ..histcap.store import DB_PATH as _HIST_DB
except Exception:                                    # pragma: no cover
    _HIST_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "data", "market_history.db")

STALE_SEC = float(os.environ.get("CHANAKYA_GREEKS_STALE_SEC", "90"))
_GREEK_SOURCE = "ANGELONE_OPTION_GREEK"
_QUOTE_SOURCE = "ANGELONE_QUOTE_FULL"


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
        q = ("SELECT DISTINCT expiry FROM option_greeks WHERE underlying=? AND source=? "
             "AND broker_status='OK'"
             + (" AND session_date_ist=?" if session_date_ist else ""))
        p = [underlying.upper(), _GREEK_SOURCE] + ([session_date_ist] if session_date_ist else [])
        return [r["expiry"] for r in conn.execute(q, p).fetchall()]

    def _target_ts(self, conn, underlying: str, expiry: str, as_of: str | None, session_date_ist: str | None = None) -> str | None:
        r = conn.execute(
            "SELECT received_ts t FROM option_greeks WHERE underlying=? AND expiry=? "
            "AND source=? AND broker_status='OK' AND julianday(received_ts)<=julianday(?) "
            "AND (server_ts IS NULL OR julianday(server_ts)<=julianday(received_ts)) "
            + ("AND session_date_ist=? " if session_date_ist else "") +
            "ORDER BY julianday(received_ts) DESC, id DESC LIMIT 1",
            (underlying.upper(), expiry, _GREEK_SOURCE, as_of or _now()) + ((session_date_ist,) if session_date_ist else ())).fetchone()
        return r["t"] if r and r["t"] else None

    def _greek_rows(self, conn, underlying: str, expiry: str, ts: str) -> list[dict]:
        return [dict(r) for r in conn.execute(
            "SELECT strike, option_type, delta, gamma, theta, vega, iv, server_ts "
            "FROM option_greeks WHERE underlying=? AND expiry=? AND broker_status='OK' "
            "AND received_ts=? AND source=? "
            "AND (server_ts IS NULL OR julianday(server_ts)<=julianday(received_ts))",
            (underlying.upper(), expiry, ts, _GREEK_SOURCE)).fetchall()]

    def _oi_map(self, conn, underlying: str, expiry: str, ts: str, session_date_ist: str | None = None) -> tuple[dict, float | None, float | None]:
        """{(strike, side): oi} at exactly `ts` (same capture cycle), plus the
        min/max option strike captured (for the coverage band)."""
        rows = conn.execute(
            "SELECT strike, option_type, oi FROM quote_snapshots WHERE symbol=? AND expiry=? "
            "AND kind='OPTION' AND received_ts=? AND source=? "
            "AND (exch_ts IS NULL OR julianday(exch_ts)<=julianday(received_ts))"
            + (" AND session_date_ist=?" if session_date_ist else ""),
            (underlying.upper(), expiry, ts, _QUOTE_SOURCE) + ((session_date_ist,) if session_date_ist else ())).fetchall()
        m = {(float(r["strike"]), str(r["option_type"]).upper()): r["oi"]
             for r in rows if r["strike"] is not None}
        strikes = [float(r["strike"]) for r in rows if r["strike"] is not None]
        return m, (min(strikes) if strikes else None), (max(strikes) if strikes else None)

    def _underlying_price(self, conn, underlying: str, ts: str) -> tuple[float | None, str | None]:
        quote = self._underlying_quote(conn, underlying, ts)
        return (float(quote["ltp"]), f"ANGELONE_QUOTE:{quote['kind']}") if quote else (None, None)

    def _underlying_quote(self, conn, underlying: str, ts: str) -> dict | None:
        """Latest valid AngelOne quote at/before capture, within STALE_SEC.

        Multiple eligible futures identities need an explicit mapping; use an
        index quote instead of guessing which future underlies the option.
        """
        from ..histcap.read_contract import IST, expiry_date, finite, instant
        day = instant(ts).astimezone(IST).date()
        for kind in ("FUTURE", "INDEX"):
            rows = conn.execute(
                "SELECT * FROM quote_snapshots WHERE symbol=? AND kind=? AND source=? "
                "AND julianday(received_ts)<=julianday(?) "
                "AND julianday(received_ts)>=julianday(?)-?/86400.0 "
                "AND (exch_ts IS NULL OR julianday(exch_ts)<=julianday(?)) "
                "ORDER BY julianday(received_ts) DESC, id DESC",
                (underlying.upper(), kind, _QUOTE_SOURCE, ts, ts, STALE_SEC, ts)).fetchall()
            valid = []
            for r in rows:
                if not finite(r["ltp"], positive=True):
                    continue
                if kind == "FUTURE":
                    try:
                        if expiry_date(r["expiry"]) < day:
                            continue
                    except ValueError:
                        continue
                valid.append(dict(r))
            if len({(r["instrument_key"], r["expiry"]) for r in valid}) == 1:
                return valid[0]
        return None

    # ------------------------------------------------------------------ run
    def run_once(self, underlying: str = "NIFTY", expiries: list[str] | None = None,
                 as_of: str | None = None, mode: str = "ONCE",
                 session_date_ist: str | None = None, deadline_sec: float | None = None) -> dict:
        """session_date_ist (CYCLE mode: today) scopes every read to that one session day; deadline_sec aborts any single statement that runs past the
        budget (the remaining expiries are recorded as errors, the run row is still closed). Without them behaviour is unchanged (ONCE/BACKFILL)."""
        with self._lock, self._conn() as conn:
            if deadline_sec:
                t_end = time.time() + float(deadline_sec)
                conn.set_progress_handler(lambda: 1 if time.time() > t_end else 0, 50000)
            rid = conn.execute(
                "INSERT INTO greek_engine_runs(started_ts, mode, underlying, expiries_json) "
                "VALUES (?,?,?,?)", (_now(), mode, underlying.upper(),
                                     json.dumps(expiries or []))).lastrowid
            conn.commit()

            exps = expiries or self._expiries(conn, underlying, session_date_ist)
            written, quality, errors, provenance = 0, {}, [], {}
            for exp in exps:
                try:
                    ts = self._target_ts(conn, underlying, exp, as_of, session_date_ist)
                    if not ts:
                        errors.append({"expiry": exp, "reason": "no OK greek rows"})
                        quality[exp] = "NO_DATA"
                        continue
                    greeks = self._greek_rows(conn, underlying, exp, ts)
                    oi_map, s_lo, s_hi = self._oi_map(conn, underlying, exp, ts, session_date_ist)
                    uq = self._underlying_quote(conn, underlying, ts)
                    px = float(uq["ltp"]) if uq else None
                    px_src = f"ANGELONE_QUOTE:{uq['kind']}" if uq else None
                    provenance[exp] = {"greek_source": _GREEK_SOURCE, "iv_source": _GREEK_SOURCE,
                        "oi_source": _QUOTE_SOURCE, "capture_timestamp": ts, "expiry": exp,
                        "underlying_quote": {k: uq[k] for k in
                            ("id", "source", "instrument_key", "expiry", "received_ts", "exch_ts", "raw_id")}
                            if uq else {"status": "UNAVAILABLE", "reason": "NO_UNAMBIGUOUS_ASOF_QUOTE"}}

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
                    conn.commit()                       # release SQLite's single writer lock BEFORE the next expiry's heavy reads (an open write transaction here starved the 3-second priority capture of live trades for minutes)
                    quality[exp] = snap["quality"]
                except Exception as e:
                    errors.append({"expiry": exp, "error": f"{type(e).__name__}: {e}"})

            if deadline_sec:
                conn.set_progress_handler(None, 0)               # the closing UPDATE must never be interrupted
            conn.execute(
                "UPDATE greek_engine_runs SET ended_ts=?, snapshots_written=?, quality_json=?, "
                "errors_json=?, notes=? WHERE id=?",
                (_now(), written, json.dumps(quality), json.dumps(errors),
                 json.dumps({"expiries": exps, "input_provenance": provenance}), rid))
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
            clauses.append("julianday(as_of_ts) <= julianday(?)"); p.append(as_of)
        if since:
            clauses.append("julianday(as_of_ts) >= julianday(?)"); p.append(since)
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
