"""Live option-flow (put-call parity) feed for NIFTY and SENSEX. OBSERVATION ONLY: own SQLite file, daemon thread, never an input to any autoscalp decision, no Telegram.
Rule and evaluation plan: data/research/option_flow/SPEC_BIDASK_PILOT_2026-10-24.md (step 4). Kill switch: CHANAKYA_OPTION_FLOW_SHADOW=0."""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import pivot_reversal_shadow as _pv, renko_shadow as _rsh
from ..candle_ai import option_flow_quotes as Q

_log = logging.getLogger("autoscalp.option_flow_shadow")
SYMBOLS = {"NIFTY": 50, "SENSEX": 100}
IST = timezone(timedelta(hours=5, minutes=30))
_DB = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "option_flow_live.db"))
_SCHEMA = """CREATE TABLE IF NOT EXISTS flow_live (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, bar_start TEXT NOT NULL, key_ts INTEGER NOT NULL, status TEXT NOT NULL, spot REAL, K REAL, expiry TEXT,
  F3m REAL, F3c REAL, F3z REAL, spread_w REAL, quote_age_s REAL, c_mid REAL, p_mid REAL, logged_ts REAL NOT NULL, fwd30_bp REAL, fwd15_bp REAL, UNIQUE (symbol, key_ts));"""
_last: dict = {}
_lock = threading.Lock()


def enabled() -> bool:
    if os.environ.get("CHANAKYA_OPTION_FLOW_SHADOW", "1") in ("0", "false", "no"): return False
    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get("CHANAKYA_OPTION_FLOW_DB"): return False
    return True


def _db() -> str: return os.environ.get("CHANAKYA_OPTION_FLOW_DB", _DB)


def _conn(path=None):
    c = sqlite3.connect(path or _db(), timeout=5); c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=5000"); c.executescript(_SCHEMA)
    try: c.execute("ALTER TABLE flow_live ADD COLUMN fwd15_bp REAL")                           # migration for a log created before the 15-minute column (pre-registered horizon extension)
    except sqlite3.OperationalError: pass
    return c


def observe(symbol: str, now_epoch: float | None = None, bars_provider=None, hist_db: str | None = None, db_path: str | None = None) -> dict | None:
    sym = symbol.upper()
    if sym not in SYMBOLS: return None
    now = now_epoch or time.time(); bars = (bars_provider or _rsh.recent_bars)(sym, "INDEX", now, 6, *( [hist_db] if (hist_db and bars_provider is None) else [] ))
    five = _pv.five_minute(bars, now)
    if len(five) < 8 or not len(bars["t"]): return None
    bin_start = int(pd.Timestamp(five["t"].iloc[-1]).timestamp()); key = bin_start + 4 * 60                       # epoch of the last minute's bar start of the newest complete 5m bin
    spot = pd.Series(bars["c"], index=bars["t"].astype(np.int64)); spot = spot[~spot.index.duplicated(keep="last")]
    day_ist = datetime.fromtimestamp(key, IST).strftime("%Y-%m-%d")
    with _lock:
        fresh = _last.get(sym) != key; _last[sym] = key
    with _conn(db_path) as c:
        for col, mins in (("fwd30_bp", 30), ("fwd15_bp", 15)):                                                                                  # forward fill
            for rid, kts, s0 in c.execute(f"SELECT id, key_ts, spot FROM flow_live WHERE symbol=? AND {col} IS NULL AND key_ts<=?", (sym, key - mins * 60)).fetchall():
                fut = spot.get(kts + mins * 60)
                if fut is not None and s0 and datetime.fromtimestamp(kts + mins * 60, IST).date() == datetime.fromtimestamp(kts, IST).date(): c.execute(f"UPDATE flow_live SET {col}=? WHERE id=?", (round((float(fut) / s0 - 1) * 1e4, 3), rid))
        if not fresh: return None
        s_now, s_prev = spot.get(key), spot.get(key - 900); row = {"symbol": sym, "bar_start": datetime.fromtimestamp(bin_start, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "key_ts": key, "logged_ts": now}
        if s_now is None or s_prev is None: return None
        h = sqlite3.connect(f"file:{hist_db or _rsh._HIST_DB}?mode=ro", uri=True, timeout=2.0)
        try:
            ex = Q.nearest_expiry(h, sym, day_ist); f = Q.parity_features(h, sym, ex, SYMBOLS[sym], float(s_now), float(s_prev), key + 60) if ex else None
        finally:
            h.close()
        if f is None: row.update(status="NO_QUOTES", spot=float(s_now), expiry=ex)
        else: row.update(status="OK", spot=float(s_now), K=f["K"], expiry=ex, F3m=f["F3m"], F3c=f["F3c"], F3z=f["F3z"], spread_w=f["spread_w"], quote_age_s=f["quote_age_s"], c_mid=f["c_mid"], p_mid=f["p_mid"])
        cols = list(row); c.execute(f"INSERT OR IGNORE INTO flow_live ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [row[k] for k in cols])
    return row


def observe_async(symbol: str) -> None:
    if not enabled(): return
    def _run():
        try: observe(symbol)
        except Exception as e: _log.debug("option flow shadow failed for %s (cosmetic): %r", symbol, e)
    threading.Thread(target=_run, name="optflow-shadow", daemon=True).start()


def forward_report(db_path: str | None = None) -> dict:
    """Running forward statistics per index (read-only). No conclusion before 60 sessions / 3000 points (fixed in the spec)."""
    from ..candle_ai import evaluate as E
    p = db_path or _db()
    if not os.path.exists(p): return {"symbols": {}, "note": "no data yet"}
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    try: d = pd.read_sql_query("SELECT symbol, bar_start, status, F3m, F3c, F3z, fwd30_bp, fwd15_bp FROM flow_live", c)
    finally: c.close()
    out = {}
    for sym, g in d.groupby("symbol"):
        ok = g[(g["status"] == "OK") & g["fwd30_bp"].notna() & g["F3m"].notna()]; days = ok["bar_start"].str[:10]
        r = {"logged": int(len(g)), "with_quotes": int((g["status"] == "OK").sum()), "no_quotes": int((g["status"] != "OK").sum()), "evaluated": int(len(ok)), "sessions": int(days.nunique())}
        if len(ok) >= 30 and (ok["fwd30_bp"] > 0).nunique() == 2:
            y = (ok["fwd30_bp"] > 0).astype(int).values; r["auc_F3m"] = E.auc(y, ok["F3m"].values); r["auc_F3c"] = E.auc(y, ok["F3c"].values)
            ok15 = g[(g["status"] == "OK") & g["fwd15_bp"].notna() & g["F3m"].notna()]
            if len(ok15) >= 30 and (ok15["fwd15_bp"] > 0).nunique() == 2: r["auc_F3m_15m"] = E.auc((ok15["fwd15_bp"] > 0).astype(int).values, ok15["F3m"].values); r["evaluated_15m"] = int(len(ok15))
            if len(ok) >= 90: r["tercile_mean_30m_bp"] = ok.groupby(pd.qcut(ok["F3m"], 3, labels=["low", "mid", "high"], duplicates="drop"), observed=True)["fwd30_bp"].mean().round(2).to_dict()
        r["conclusion_allowed"] = bool(r["sessions"] >= 60 or r["evaluated"] >= 3000); out[sym] = r
    return {"symbols": out, "rule": "no conclusion before 60 sessions or 3000 evaluated points per index; then replication criterion: univariate AUC of F3m > 0.510 (SPEC_BIDASK_PILOT)"}
