"""Resumable, checkpointed, idempotent backfill of Dhan expired-option bars.

One checkpoint row per (underlying, option_type, strike, window, interval, flag, code).
A DONE window is never re-fetched; ERROR / NO_DATA windows are retried on the next
run. Rows land in hist_options.db (own file) via HistOptionStore (INSERT OR IGNORE
on the contract key), so a crashed or repeated run cannot duplicate bars.

Windows are [from, to) with to non-inclusive and <= 30 days (Dhan's documented
per-request limit). Nothing is written for a failed/empty window; a window is only
DONE when bars were fetched and stored. Greeks follow rolling.normalize: expiry
unresolved -> UNAVAILABLE (e.g. before the broker-metadata range), never guessed.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, timedelta
from typing import Callable

from . import quality, rolling
from .expiry import ExpiryResolver
from .expiry_store import ExpiryAudit
from .store import HistOptionStore

MAX_WINDOW_DAYS = 30
DONE, ERROR, NO_DATA = "DONE", "ERROR", "NO_DATA"


def plan_windows(start: date, end: date, days: int = MAX_WINDOW_DAYS) -> list[tuple[date, date]]:
    """Contiguous [from, to) windows covering [start, end); `to` exclusive."""
    if days < 1 or days > MAX_WINDOW_DAYS:
        raise ValueError(f"window must be 1..{MAX_WINDOW_DAYS} days")
    out, cur = [], start
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        out.append((cur, nxt))
        cur = nxt
    return out


class Checkpoints:
    def __init__(self, store: HistOptionStore):
        self.store = store
        with store._c() as c:
            c.execute("CREATE TABLE IF NOT EXISTS backfill_checkpoint ("
                      "underlying TEXT, option_type TEXT, strike TEXT, interval INTEGER, expiry_flag TEXT, "
                      "expiry_code INTEGER, window_from TEXT, window_to TEXT, status TEXT, bars INTEGER, "
                      "rows_written INTEGER, report_json TEXT, error TEXT, updated_ts TEXT, "
                      "PRIMARY KEY (underlying, option_type, strike, interval, expiry_flag, expiry_code, "
                      "window_from, window_to))")

    def status(self, key) -> str | None:
        with self.store._c() as c:
            r = c.execute("SELECT status FROM backfill_checkpoint WHERE underlying=? AND option_type=? AND strike=? "
                          "AND interval=? AND expiry_flag=? AND expiry_code=? AND window_from=? AND window_to=?",
                          key).fetchone()
        return r[0] if r else None

    def set(self, key, status, *, bars=0, rows=0, report=None, error=None):
        from datetime import datetime, timezone
        with self.store._c() as c:
            c.execute("INSERT OR REPLACE INTO backfill_checkpoint VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (*key, status, bars, rows, json.dumps(report) if report else None, error,
                       datetime.now(timezone.utc).isoformat()))

    def summary(self, underlying: str | None = None) -> dict:
        q = "SELECT status, COUNT(*), COALESCE(SUM(rows_written),0) FROM backfill_checkpoint"
        p: list = []
        if underlying:
            q += " WHERE underlying=?"; p.append(underlying)
        with self.store._c() as c:
            return {s: {"windows": n, "rows": r} for s, n, r in c.execute(q + " GROUP BY status", p)}


def run(underlying: str, start: date, end: date, *, strikes=("ATM",), types=("CE", "PE"), interval: int = 1,
        flag: str = "WEEK", code: int = 1, window_days: int = MAX_WINDOW_DAYS, sleep_sec: float = 0.5,
        max_windows: int | None = None, store: HistOptionStore | None = None,
        fetch: Callable | None = None, resolver: ExpiryResolver | None = None, audit: ExpiryAudit | None = None,
        r: float = 0.0, q: float = 0.0, preflight: Callable[[], bool] | None = None) -> dict:
    """Backfill [start, end). Returns counts. `fetch(underlying, ot, from, to, strike=..., ...)` is injectable."""
    if rolling.support_status(underlying) != "VERIFIED":
        return {"status": "UNSUPPORTED", "reason": rolling.support_status(underlying)}
    # Auth check before touching any checkpoint: an expired session must not be
    # recorded as 'no data'. Skipped when a fetch function is injected (tests).
    if preflight is None and fetch is None:
        preflight = rolling.session_ok
    if preflight is not None and not preflight():
        return {"status": "AUTH_UNAVAILABLE", "reason": "Dhan session missing/expired (re-login needed)"}
    store = store or HistOptionStore()
    cps = Checkpoints(store)
    fetch = fetch or rolling.fetch_side
    audit = audit if audit is not None else ExpiryAudit(store.path)
    ef = {ot: rolling.resolver_expiry_for(underlying, flag=flag, code=code, resolver=resolver, audit=audit)
          for ot in types}
    out = {"fetched": 0, "skipped_done": 0, "errors": 0, "no_data": 0, "rows_written": 0, "windows_planned": 0}
    attempted = 0
    for (w0, w1) in plan_windows(start, end, window_days):
        for strike in strikes:
            for ot in types:
                out["windows_planned"] += 1
                key = (underlying.upper(), ot, strike, interval, flag, code, w0.isoformat(), w1.isoformat())
                if cps.status(key) == DONE:
                    out["skipped_done"] += 1
                    continue
                if max_windows is not None and attempted >= max_windows:
                    return {**out, "status": "STOPPED_MAX_WINDOWS", "checkpoints": cps.summary(underlying.upper())}
                attempted += 1
                try:
                    side = fetch(underlying, ot, w0.isoformat(), w1.isoformat(), strike=strike, interval=interval,
                                 expiry_flag=flag, expiry_code=code)
                    if not side:
                        cps.set(key, NO_DATA, error="empty_or_failed_response")
                        out["no_data"] += 1
                    else:
                        rows = rolling.normalize(side, underlying=underlying, option_type=ot, interval_min=interval,
                                                 expiry_for=ef[ot], r=r, q=q)
                        written = store.write(rows)
                        rep = quality.build_report(rows, interval_min=interval)
                        cps.set(key, DONE, bars=len(rows), rows=written,
                                report={k: rep.get(k) for k in ("rows", "date_coverage", "missing_bars_total",
                                                                "duplicate_records", "missing_greeks",
                                                                "greek_source_counts", "completeness_pct")})
                        out["fetched"] += 1
                        out["rows_written"] += written
                except Exception as e:           # one bad window never aborts the run
                    cps.set(key, ERROR, error=f"{type(e).__name__}: {str(e)[:200]}")
                    out["errors"] += 1
                if sleep_sec:
                    time.sleep(sleep_sec)
    return {**out, "status": "COMPLETE" if not (out["errors"] or out["no_data"]) else "INCOMPLETE",
            "checkpoints": cps.summary(underlying.upper())}
