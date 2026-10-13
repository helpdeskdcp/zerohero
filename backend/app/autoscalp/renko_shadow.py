"""Renko trend-state SHADOW log for autoscalp (observation only).

Two observation streams, both in data/renko_shadow.db:
  * renko_shadow_entries - one row per autoscalp paper ENTRY (rare: ~2 BUY signals in 8 sessions).
  * renko_shadow_ticks   - one row per symbol per newly COMPLETED 1-minute bar, written from the
    decision tick regardless of whether a trade happens (PRIMARY KEY (symbol, last_bar_ts) dedupes
    the 30 s ticks). Lets the Renko trend-persistence finding be validated forward from price
    alone, in days instead of years. Non-OK states (no bars, no brick size) are NOT written.

For every autoscalp paper entry this records the causal Renko state of the
underlying (direction, run length, brick size, alignment with the trade side) into
its OWN SQLite file (data/renko_shadow.db). It never reads or writes chanakya.db,
never changes a decision, never delays the trade: the work runs in a daemon thread
and every failure degrades to a logged status. Purpose: forward-validate the Renko
trend-persistence finding (backend/SMART_BOS_RESEARCH_REPORT.md) before it is ever
allowed near a decision. Offline analysis: scripts/renko_shadow_report.py.

Kill switch: CHANAKYA_RENKO_SHADOW=0.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np

from ..research_engines.smart_bos import renko as _renko

_log = logging.getLogger("autoscalp.renko_shadow")
_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.abspath(os.path.join(_HERE, "..", "..", "data", "renko_shadow.db"))
_HIST_DB = os.path.abspath(os.path.join(_HERE, "..", "..", "data", "market_history.db"))

# frozen research parameters (SMART_BOS_RESEARCH_REPORT.md): k x mean prior-5-session range
K_FROZEN, LOOKBACK, MIN_RUN = 0.12, 5, 3
ALT_K = (0.08, 0.16)
# symbol -> (market_history kind, regular session minute-of-day [start,end), mintick)
SERIES = {
    "NIFTY": ("INDEX", (555, 930), 0.05), "BANKNIFTY": ("INDEX", (555, 930), 0.05),
    "SENSEX": ("INDEX", (555, 930), 0.05),
    "NATURALGAS": ("FUTURE", (540, 1410), 0.1), "CRUDEOIL": ("FUTURE", (540, 1410), 1.0),
}
_SCHEMA = """CREATE TABLE IF NOT EXISTS renko_shadow_entries (
  trade_id TEXT PRIMARY KEY, signal_id TEXT, logged_ts TEXT, entry_ts TEXT, symbol TEXT, option_type TEXT,
  trade_dir INTEGER, status TEXT, reason TEXT, brick REAL, renko_dir INTEGER, renko_run INTEGER,
  aligned INTEGER, aligned_run3 INTEGER, bricks_today INTEGER, last_bar_ts TEXT, bar_lag_sec REAL,
  bars_used INTEGER, alt_json TEXT, underlying_ltp REAL, params_json TEXT)"""


def enabled() -> bool:
    if os.environ.get("CHANAKYA_RENKO_SHADOW", "1") in ("0", "false", "no"):
        return False
    # Never let a test run write into the production shadow log: under pytest the hook is
    # inert unless a test explicitly points CHANAKYA_RENKO_SHADOW_DB at its own file.
    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get("CHANAKYA_RENKO_SHADOW_DB"):
        return False
    return True


def _db_path() -> str:
    return os.environ.get("CHANAKYA_RENKO_SHADOW_DB", _DEFAULT_DB)


def _iso_to_epoch(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def recent_bars(symbol: str, kind: str, until_epoch: float, days: int = 12, db_path: str | None = None) -> dict:
    """1-minute bars that were COMPLETE at `until_epoch` (bar_start + 60s <= until). Read-only, indexed, bounded."""
    lo = datetime.fromtimestamp(until_epoch - days * 86400, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    hi = datetime.fromtimestamp(until_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    conn = sqlite3.connect(f"file:{db_path or _HIST_DB}?mode=ro", uri=True, timeout=1.0)
    try:
        rows = conn.execute("SELECT bar_start, o, h, l, c FROM market_candles WHERE symbol=? AND kind=? AND tf='1m' "
                            "AND bar_start>=? AND bar_start<? ORDER BY bar_start", (symbol, kind, lo, hi)).fetchall()
    finally:
        conn.close()
    seen, t, o, h, l, c = set(), [], [], [], [], []
    for r in rows:
        e = int(_iso_to_epoch(r[0]))
        if e in seen or e + 60 > until_epoch or None in r[1:]:
            continue
        seen.add(e); t.append(e); o.append(r[1]); h.append(r[2]); l.append(r[3]); c.append(r[4])
    order = np.argsort(t)
    return {"t": np.asarray(t, np.int64)[order], "o": np.asarray(o)[order], "h": np.asarray(h)[order],
            "l": np.asarray(l)[order], "c": np.asarray(c)[order], "v": np.zeros(len(t))}


def compute_state(bars: dict, k: float, session, mintick: float) -> dict:
    """Renko state at the LAST available bar (causal: only bars already complete)."""
    if len(bars["t"]) < 30:
        return {"status": "INSUFFICIENT_BARS", "bars": int(len(bars["t"]))}
    rs = _renko.build(bars, k=k, lookback=LOOKBACK, mintick=mintick, session=session)
    d, run, brick = rs.state_at(len(bars["t"]) - 1)
    if brick <= 0:
        return {"status": "NO_BRICK_SIZE", "bars": int(len(bars["t"]))}
    return {"status": "OK", "dir": d, "run": run, "brick": brick, "bricks_today": int(rs.brick_count[-1]),
            "bars": int(len(bars["t"]))}


def build_record(*, trade_id, signal_id, symbol, option_type, entry_ts, underlying_ltp, bars_provider=None) -> dict:
    sym = symbol.upper()
    trade_dir = 1 if option_type == "CE" else -1 if option_type == "PE" else 0
    rec = {"trade_id": trade_id, "signal_id": signal_id, "logged_ts": datetime.now(timezone.utc).isoformat(),
           "entry_ts": entry_ts, "symbol": sym, "option_type": option_type, "trade_dir": trade_dir,
           "underlying_ltp": underlying_ltp, "params_json": json.dumps({"k": K_FROZEN, "lookback": LOOKBACK,
                                                                       "min_run": MIN_RUN, "alt_k": ALT_K})}
    spec = SERIES.get(sym)
    if spec is None or trade_dir == 0:
        return {**rec, "status": "UNSUPPORTED", "reason": "no 1m series configured" if spec is None else "bad option_type"}
    kind, session, mintick = spec
    until = _iso_to_epoch(entry_ts)
    try:
        bars = (bars_provider or recent_bars)(sym, kind, until)
    except Exception as e:
        return {**rec, "status": "DATA_ERROR", "reason": f"{type(e).__name__}: {str(e)[:120]}"}
    st = compute_state(bars, K_FROZEN, session, mintick)
    if st["status"] != "OK":
        return {**rec, "status": st["status"], "reason": f"bars={st['bars']}"}
    last = int(bars["t"][-1])
    alts = {}
    for ak in ALT_K:
        a = compute_state(bars, ak, session, mintick)
        alts[str(ak)] = {"dir": a.get("dir"), "run": a.get("run"), "brick": a.get("brick")} if a["status"] == "OK" else None
    aligned = int(st["dir"] == trade_dir)
    return {**rec, "status": "OK", "reason": None, "brick": st["brick"], "renko_dir": st["dir"],
            "renko_run": st["run"], "aligned": aligned, "aligned_run3": int(aligned and st["run"] >= MIN_RUN),
            "bricks_today": st["bricks_today"], "last_bar_ts": datetime.fromtimestamp(last, tz=timezone.utc).isoformat(),
            "bar_lag_sec": until - (last + 60), "bars_used": st["bars"], "alt_json": json.dumps(alts)}


_COLS = ("trade_id", "signal_id", "logged_ts", "entry_ts", "symbol", "option_type", "trade_dir", "status", "reason",
         "brick", "renko_dir", "renko_run", "aligned", "aligned_run3", "bricks_today", "last_bar_ts", "bar_lag_sec",
         "bars_used", "alt_json", "underlying_ltp", "params_json")


def write_record(rec: dict, db_path: str | None = None) -> None:
    path = db_path or _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    try:
        conn.execute(_SCHEMA)
        conn.execute(f"INSERT OR IGNORE INTO renko_shadow_entries ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
                     [rec.get(c) for c in _COLS])
        conn.commit()
    finally:
        conn.close()


def log_entry(*, trade_id, signal_id, symbol, option_type, entry_ts, underlying_ltp, bars_provider=None,
              db_path=None) -> dict:
    """Synchronous core (used by tests and the async wrapper). Never raises."""
    try:
        rec = build_record(trade_id=trade_id, signal_id=signal_id, symbol=symbol, option_type=option_type,
                           entry_ts=entry_ts, underlying_ltp=underlying_ltp, bars_provider=bars_provider)
        write_record(rec, db_path)
        return rec
    except Exception as e:                                   # shadow logging must never affect trading
        _log.warning("renko shadow log failed for %s: %r", trade_id, e)
        return {"status": "LOG_ERROR", "reason": repr(e)}


def log_entry_async(**kw) -> None:
    """Fire-and-forget: returns immediately; no effect on the trade path."""
    if not enabled():
        return
    threading.Thread(target=log_entry, kwargs=kw, name="renko-shadow", daemon=True).start()


# ---------------------------------------------------------------- tick-level stream
_TICK_SCHEMA = """CREATE TABLE IF NOT EXISTS renko_shadow_ticks (
  symbol TEXT NOT NULL, last_bar_ts TEXT NOT NULL, logged_ts TEXT, last_close REAL, brick REAL,
  renko_dir INTEGER, renko_run INTEGER, bricks_today INTEGER, bar_lag_sec REAL, bars_used INTEGER,
  tick_price REAL, decision TEXT, no_trade_reason TEXT, regime TEXT, k REAL,
  PRIMARY KEY (symbol, last_bar_ts))"""
_TICK_COLS = ("symbol", "last_bar_ts", "logged_ts", "last_close", "brick", "renko_dir", "renko_run", "bricks_today",
              "bar_lag_sec", "bars_used", "tick_price", "decision", "no_trade_reason", "regime", "k")
_state = {"schema_ready": False, "last_error": None, "last_error_ts": None, "ticks_logged": 0, "write_errors": 0}
_last_bar: dict = {}
_inflight: set = set()
_lock = threading.Lock()


def _note_error(msg: str) -> None:
    _state["last_error"], _state["last_error_ts"] = msg, datetime.now(timezone.utc).isoformat()
    _state["write_errors"] += 1
    _log.warning("renko shadow: %s", msg)


def init_schema(db_path: str | None = None) -> bool:
    """Create BOTH tables (idempotent). The file then exists with a schema and 0 rows - a real
    'logger initialized, awaiting events' state, not an empty placeholder file."""
    path = db_path or _db_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, timeout=5.0)
        try:
            conn.execute(_SCHEMA)
            conn.execute(_TICK_SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _state["schema_ready"] = True
        return True
    except Exception as e:
        _note_error(f"init_schema failed: {type(e).__name__}: {e}")
        return False


def _latest_complete_bar(symbol: str, kind: str, now_epoch: float, db_path: str | None = None):
    """Epoch of the newest COMPLETE 1m bar (indexed, O(log n)); None if none."""
    hi = datetime.fromtimestamp(now_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    lo = datetime.fromtimestamp(now_epoch - 3 * 86400, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    conn = sqlite3.connect(f"file:{db_path or _HIST_DB}?mode=ro", uri=True, timeout=1.0)
    try:
        r = conn.execute("SELECT MAX(bar_start) FROM market_candles WHERE symbol=? AND kind=? AND tf='1m' "
                         "AND bar_start>=? AND bar_start<?", (symbol, kind, lo, hi)).fetchone()
    finally:
        conn.close()
    if not r or not r[0]:
        return None
    e = _iso_to_epoch(r[0])
    return e if e + 60 <= now_epoch else None


def observe_tick(*, symbol, tick_price=None, decision=None, reason=None, regime=None, bars_provider=None,
                 db_path=None, now_epoch=None, hist_db=None) -> dict:
    """Synchronous core. Writes at most one row per (symbol, newly completed 1m bar). Never raises."""
    try:
        sym = symbol.upper()
        spec = SERIES.get(sym)
        if spec is None:
            return {"status": "UNSUPPORTED"}
        kind, session, mintick = spec
        now = float(now_epoch if now_epoch is not None else time.time())
        if bars_provider is None:
            last = _latest_complete_bar(sym, kind, now, hist_db)
            if last is None:
                return {"status": "NO_BARS"}
            if _last_bar.get(sym) == last:
                return {"status": "UNCHANGED"}
        bars = (bars_provider or (lambda s, k, u: recent_bars(s, k, u, db_path=hist_db)))(sym, kind, now)
        st = compute_state(bars, K_FROZEN, session, mintick)
        if st["status"] != "OK":
            return {"status": st["status"]}                       # no row: nothing observed
        last_t = int(bars["t"][-1])
        row = {"symbol": sym, "last_bar_ts": datetime.fromtimestamp(last_t, tz=timezone.utc).isoformat(),
               "logged_ts": datetime.now(timezone.utc).isoformat(), "last_close": float(bars["c"][-1]),
               "brick": st["brick"], "renko_dir": st["dir"], "renko_run": st["run"],
               "bricks_today": st["bricks_today"], "bar_lag_sec": now - (last_t + 60), "bars_used": st["bars"],
               "tick_price": tick_price, "decision": decision,
               "no_trade_reason": (reason or "")[:160] or None, "regime": regime, "k": K_FROZEN}
        path = db_path or _db_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, timeout=5.0)
        try:
            conn.execute(_SCHEMA)
            conn.execute(_TICK_SCHEMA)
            cur = conn.execute(f"INSERT OR IGNORE INTO renko_shadow_ticks ({','.join(_TICK_COLS)}) "
                               f"VALUES ({','.join('?' * len(_TICK_COLS))})", [row[c] for c in _TICK_COLS])
            conn.commit()
        finally:
            conn.close()
        _last_bar[sym] = last_t
        _state["schema_ready"] = True
        if cur.rowcount:
            _state["ticks_logged"] += 1
        return {"status": "OK", "inserted": bool(cur.rowcount), **row}
    except Exception as e:                                         # observation must never affect trading
        _note_error(f"observe_tick {symbol}: {type(e).__name__}: {str(e)[:160]}")
        return {"status": "LOG_ERROR", "reason": repr(e)}


def observe_tick_async(**kw) -> None:
    """Fire-and-forget from the decision tick; one in-flight worker per symbol (no pile-up)."""
    if not enabled():
        return
    sym = str(kw.get("symbol", "")).upper()
    with _lock:
        if sym in _inflight:
            return
        _inflight.add(sym)

    def _run():
        try:
            observe_tick(**kw)
        finally:
            with _lock:
                _inflight.discard(sym)
    try:
        threading.Thread(target=_run, name="renko-shadow-tick", daemon=True).start()
    except Exception:
        with _lock:
            _inflight.discard(sym)
        raise


_status_cache = {"t": 0.0, "v": None}


def status(db_path: str | None = None) -> dict:
    """Cheap, read-only (never creates the file). Cached 5 s so /status polling stays light."""
    if time.time() - _status_cache["t"] < 5 and _status_cache["v"] is not None and db_path is None:
        return _status_cache["v"]
    path = db_path or _db_path()
    out = {"enabled": enabled(), "db_path": path, "db_exists": os.path.exists(path),
           "schema_ready_in_process": _state["schema_ready"], "ticks_logged_in_process": _state["ticks_logged"],
           "write_errors": _state["write_errors"], "last_error": _state["last_error"],
           "last_error_ts": _state["last_error_ts"]}
    if out["db_exists"]:
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.5)
            try:
                out["entries"] = conn.execute("SELECT COUNT(*) FROM renko_shadow_entries").fetchone()[0]
                n, last = conn.execute("SELECT COUNT(*), MAX(logged_ts) FROM renko_shadow_ticks").fetchone()
                out["ticks"], out["last_tick_logged_ts"] = n, last
            finally:
                conn.close()
        except Exception as e:
            out["read_error"] = f"{type(e).__name__}: {str(e)[:100]}"
    if db_path is None:
        _status_cache.update(t=time.time(), v=out)
    return out
