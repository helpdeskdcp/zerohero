"""Hybrid MP v13 (fixed) dashboard API -- READ-ONLY. Runs the research engine on the latest 1-minute bars and returns the Pine
dashboard fields (+ SMC context, today's trades, small chart payload). No order path, no writes, not used by any live decision."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

import numpy as np
from fastapi import APIRouter, HTTPException

from .research_engines.hybrid_mp.engine import fixed_params, resample, run
from .research_engines.smart_bos.data import ist_minutes, load_1m

router = APIRouter(prefix="/api/hybrid_mp", tags=["hybrid-mp"])

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_PLATFORM_DB = os.path.join(_ROOT, "data", "platform_ticks.db")
_SUMMARY = os.path.join(_ROOT, "data", "research", "hybrid_mp", "fixed_summary.json")
# platform instrument key per symbol (Angel index tokens)
_PLATFORM_KEY = {"NIFTY": "NSE:99926000", "BANKNIFTY": "NSE:99926009", "SENSEX": "BSE:99919000"}
_TTL = 10.0
_cache: dict = {}
_hist: dict = {}
_lock = threading.Lock()
SESSIONS_KEPT = 8


def _history(symbol: str) -> dict:
    """Cached 1m history (last SESSIONS_KEPT sessions) from the research cache; reloaded when the cache file changes."""
    with _lock:
        b = _hist.get(symbol)
        if b is None:
            full = load_1m(symbol, "INDEX")
            days, _ = ist_minutes(full["t"])
            keep = np.isin(days, np.unique(days)[-SESSIONS_KEPT:])
            b = {k: v[keep] for k, v in full.items()}
            _hist[symbol] = b
        return b


def _platform_1m(symbol: str, after_ts: int) -> dict | None:
    key = _PLATFORM_KEY.get(symbol)
    if not key or not os.path.exists(_PLATFORM_DB):
        return None
    c = sqlite3.connect(f"file:{_PLATFORM_DB}?mode=ro", uri=True, timeout=5)
    try:
        rows = c.execute("SELECT start_ts, o, h, l, c FROM candles WHERE instrument=? AND tf_sec=60 AND start_ts>? ORDER BY start_ts",
                         (key, int(after_ts))).fetchall()
    finally:
        c.close()
    if not rows:
        return None
    a = np.array(rows, dtype=float)
    return {"t": a[:, 0].astype(np.int64), "o": a[:, 1], "h": a[:, 2], "l": a[:, 3], "c": a[:, 4], "v": np.zeros(len(a))}


def latest_1m(symbol: str) -> tuple[dict, dict]:
    hist = _history(symbol)
    live = _platform_1m(symbol, int(hist["t"][-1]))
    b = hist if live is None else {k: np.concatenate([hist[k], live[k]]) for k in hist}
    days, mod = ist_minutes(b["t"])
    ok = (mod >= 555) & (mod < 930)
    b = {k: v[ok] for k, v in b.items()}
    meta = {"history_last_bar": int(hist["t"][-1]), "live_bars_appended": 0 if live is None else int(len(live["t"])),
            "last_bar_age_sec": int(time.time() - int(b["t"][-1])) if len(b["t"]) else None}
    return b, meta


def _nse_open(now_ist_epoch: float) -> bool:
    """NSE/BSE cash session by wall clock (IST epoch = unix + 19800): Mon-Fri 09:15-15:30. Holidays are not modelled."""
    wd = int((now_ist_epoch // 86400 + 3) % 7)                # Mon=0 (1970-01-01 was a Thursday)
    mod = int((now_ist_epoch % 86400) // 60)
    return wd < 5 and 555 <= mod < 930


def build_state(symbol: str = "NIFTY", tf: int = 5, smc: bool = False, smc_min: int = 2) -> dict:
    symbol = symbol.upper()
    if symbol not in _PLATFORM_KEY:
        raise ValueError(f"symbol must be one of {sorted(_PLATFORM_KEY)}")
    b1, meta = latest_1m(symbol)
    chart = resample(b1, tf, 555, 930)
    p = fixed_params(chart_tf=tf, smc_filter=smc, smc_min=smc_min)
    trades, st = run(chart, p, b1=b1, want_state=True)
    state = st.pop("state")
    tz = 19800
    n = len(chart["t"])
    lo = max(0, n - 150)
    last_day = int((chart["t"][-1] + tz) // 86400)
    today = [t for t in trades if t.day == last_day]
    now_ist = time.time() + tz
    market_open = _nse_open(now_ist)
    age = meta["last_bar_age_sec"]
    if not market_open:
        data_status = "MARKET_CLOSED"
    elif age is None or age > max(180, 3 * tf * 60):
        data_status = "STALE"
    else:
        data_status = "LIVE"
    meta["data_status"] = data_status
    if data_status != "LIVE":
        state["session"] = "MARKET CLOSED (state as of last bar)" if data_status == "MARKET_CLOSED" else "DATA STALE"
    state.update(
        symbol=symbol, data=meta, counters={k: v for k, v in st.items()},
        research_only=True, live_trading=False,
        candles=[{"time": int(chart["t"][k] + tz), "open": float(chart["o"][k]), "high": float(chart["h"][k]),
                  "low": float(chart["l"][k]), "close": float(chart["c"][k])} for k in range(lo, n)],
        trades_today=[{"time": int(chart["t"][t.sig_i] + tz), "side": "BUY" if t.side == 1 else "SELL", "type": t.entry_type,
                       "entry": round(t.entry, 2), "exit": round(t.exit, 2), "r": round(t.r, 2), "reason": t.reason,
                       "max_target": t.max_t} for t in today[-20:]],
    )
    return state


@router.get("/state")
def state(symbol: str = "NIFTY", tf: int = 5, smc: int = 0, smc_min: int = 2):
    if tf not in (1, 3, 5, 15):
        raise HTTPException(400, "tf must be 1, 3, 5 or 15")
    key = (symbol.upper(), tf, smc, smc_min)
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    try:
        out = build_state(symbol, tf, bool(smc), smc_min)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _cache[key] = (now, out)
    return out


@router.get("/backtest")
def backtest_summary():
    """Saved research result of the fixed version (written by scripts/hybrid_mp_summary.py); not recomputed per request."""
    if not os.path.exists(_SUMMARY):
        return {"available": False}
    with open(_SUMMARY) as f:
        return {"available": True, **json.load(f)}
