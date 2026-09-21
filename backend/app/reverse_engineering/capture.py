"""
Phase 4 -- event-window capture. Given a symbol/contract and a reference
timestamp T0 (a public signal's reported time, or any research anchor),
build the T-30m...T+30m window from data that was ACTUALLY captured --
never interpolated, never fabricated.

Data sources (both read-only, both pre-existing in this codebase):
  - data/market_history.db :: market_candles  (index/future OHLC bars)
  - data/market_history.db :: quote_snapshots  (option LTP/OI/bid/ask)

This module never writes to market_history.db.

TOLERANCE: a window slot is populated from the nearest real row within
+/- _TOLERANCE_SEC of the target offset time, else the slot is `None`
(never a synthetic fill). 90s was chosen because this repo's own capture
density (see PHASE0_PHASE1_REPORT.md: ~2-3 option snapshots/strike/day) is
far coarser than 1 minute -- a tighter tolerance would leave nearly every
slot empty, a looser one risks blending a slot with its neighbour.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

_TOLERANCE_SEC = 90

_OFFSETS_MIN = [-30, -15, -10, -5, -3, -1, 0, 1, 3, 5, 10, 15, 30]

MARKET_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "market_history.db"))


def _slot_label(offset_min: int) -> str:
    if offset_min == 0:
        return "T0"
    sign = "+" if offset_min > 0 else "-"
    return f"T{sign}{abs(offset_min)}m"


def _parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _nearest_row(conn: sqlite3.Connection, *, table: str, ts_col: str,
                 where_sql: str, where_params: tuple, target: datetime) -> dict | None:
    """Nearest row to `target` within +/- _TOLERANCE_SEC, by real elapsed
    time (not just a same-day scan) -- computed in Python because SQLite
    has no native ISO-timestamp arithmetic here."""
    lo = (target - timedelta(seconds=_TOLERANCE_SEC * 4)).isoformat()
    hi = (target + timedelta(seconds=_TOLERANCE_SEC * 4)).isoformat()
    # Widen the SQL prefilter beyond the tolerance (cheap, index-friendly),
    # then apply the exact tolerance check in Python.
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE {where_sql} AND {ts_col}>=? AND {ts_col}<=? "
        f"ORDER BY {ts_col}", (*where_params, lo, hi)).fetchall()
    best, best_delta = None, None
    for row in rows:
        try:
            row_ts = _parse_iso(row[ts_col])
        except (TypeError, ValueError):
            continue
        delta = abs((row_ts - target).total_seconds())
        if delta <= _TOLERANCE_SEC and (best_delta is None or delta < best_delta):
            best, best_delta = row, delta
    return dict(best) if best is not None else None


def _connect(db_path: str) -> sqlite3.Connection | None:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
        return None


def index_window(underlying: str, t0_iso: str, *, db_path: str | None = None) -> dict:
    """T-30m..T+30m of real 1m INDEX candles for `underlying` around T0.
    Each slot is either a real market_candles row's o/h/l/c/v or None."""
    path = db_path or MARKET_DB_PATH
    conn = _connect(path)
    result = {}
    if conn is None:
        return {_slot_label(m): None for m in _OFFSETS_MIN}
    try:
        t0 = _parse_iso(t0_iso)
        for m in _OFFSETS_MIN:
            target = t0 + timedelta(minutes=m)
            row = _nearest_row(
                conn, table="market_candles", ts_col="bar_start",
                where_sql="symbol=? AND kind='INDEX' AND tf='1m'",
                where_params=(underlying,), target=target)
            result[_slot_label(m)] = (
                {"ts": row["bar_start"], "o": row["o"], "h": row["h"],
                 "l": row["l"], "c": row["c"], "v": row["v"]} if row else None)
        return result
    finally:
        conn.close()


def option_window(underlying: str, expiry: str, strike: float, ce_pe: str, t0_iso: str, *,
                  db_path: str | None = None) -> dict:
    """T-30m..T+30m of real option quote_snapshots (ltp/oi/oi_change/bid/ask/
    volume) for the given contract around T0. None where no real snapshot
    falls within tolerance -- this is expected to be sparse (see
    PHASE0_PHASE1_REPORT.md) and must be reported as such, not filled."""
    path = db_path or MARKET_DB_PATH
    conn = _connect(path)
    result = {}
    if conn is None:
        return {_slot_label(m): None for m in _OFFSETS_MIN}
    try:
        t0 = _parse_iso(t0_iso)
        for m in _OFFSETS_MIN:
            target = t0 + timedelta(minutes=m)
            row = _nearest_row(
                conn, table="quote_snapshots", ts_col="exch_ts",
                where_sql=("symbol=? AND kind='OPTION' AND expiry=? "
                          "AND strike=? AND option_type=? AND exch_ts IS NOT NULL"),
                where_params=(underlying, expiry, strike, ce_pe), target=target)
            result[_slot_label(m)] = (
                {"ts": row["exch_ts"], "ltp": row["ltp"], "bid": row["bid"], "ask": row["ask"],
                 "volume": row["volume"], "oi": row["oi"], "oi_change": row["oi_change"]}
                if row else None)
        return result
    finally:
        conn.close()


def build_event_window(underlying: str, t0_iso: str, *, expiry: str | None = None,
                       strike: float | None = None, ce_pe: str | None = None,
                       db_path: str | None = None) -> dict:
    """Combined index + (optional) option window. `data_quality` reports the
    fraction of the 13 slots that were actually populated per series --
    an honest coverage number, not a confidence score."""
    idx = index_window(underlying, t0_iso, db_path=db_path)
    opt = None
    if expiry is not None and strike is not None and ce_pe is not None:
        opt = option_window(underlying, expiry, strike, ce_pe, t0_iso, db_path=db_path)

    def _coverage(window: dict | None) -> float | None:
        if window is None:
            return None
        n = len(window)
        return round(sum(1 for v in window.values() if v is not None) / n, 3) if n else None

    return {
        "underlying": underlying, "t0": t0_iso, "expiry": expiry, "strike": strike, "ce_pe": ce_pe,
        "index_window": idx, "option_window": opt,
        "index_coverage": _coverage(idx), "option_coverage": _coverage(opt),
    }
