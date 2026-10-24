"""Pivot-line reversal SIGNAL feed for the index watchlist (NIFTY / BANKNIFTY / SENSEX): TA-Lib engulfing / harami at a classical or Fibonacci pivot line after a consolidation box
(rule: data/research/pivot_reversal/SPEC_2026-10-24.md, code: app/candle_ai/pivot_reversal.py). OBSERVATION ONLY: own SQLite file, daemon thread, never an input to any autoscalp
decision, never blocks the tick. Every fired signal is logged once and its forward return (+3 / +6 / +12 five-minute bars, gross bps) is filled in later, so the rule is validated forward.
The historical study found NO demonstrated net edge (INSUFFICIENT_SAMPLE by the pre-registered gate; all 16 cells within -5..+2 bp), so every message says so.
Kill switches: CHANAKYA_PIVOT_SHADOW=0 (all), CHANAKYA_PIVOT_SHADOW_TG=0 (only the Telegram message)."""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import renko_shadow as _rsh

_log = logging.getLogger("autoscalp.pivot_shadow")
SYMBOLS = ("NIFTY", "BANKNIFTY", "SENSEX")
HORIZONS = (3, 6, 12)
_DB = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "pivot_reversal_shadow.db"))
_SCHEMA = """CREATE TABLE IF NOT EXISTS pivot_signals (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, bar_start TEXT NOT NULL, variant TEXT NOT NULL, side TEXT NOT NULL, close REAL,
  logged_ts REAL NOT NULL, fwd3 REAL, fwd6 REAL, fwd12 REAL, rh REAL, rl REAL, UNIQUE (symbol, bar_start, variant));"""
_last_bar: dict = {}
_lock = threading.Lock()
SIDE = {"ENGULF_BUY": "BUY", "HARAMI_BUY": "BUY", "ENGULF_SELL": "SELL", "HARAMI_SELL": "SELL", "RZ_BRK_UP": "BUY", "RZ_FADE_LONG": "BUY", "RZ_BRK_DN": "SELL", "RZ_FADE_SHORT": "SELL"}


def enabled() -> bool:
    if os.environ.get("CHANAKYA_PIVOT_SHADOW", "1") in ("0", "false", "no"): return False
    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get("CHANAKYA_PIVOT_SHADOW_DB"): return False          # tests never write the production log
    return True


def _db() -> str: return os.environ.get("CHANAKYA_PIVOT_SHADOW_DB", _DB)


def _conn(path=None):
    c = sqlite3.connect(path or _db(), timeout=5); c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=5000"); c.executescript(_SCHEMA)
    for col in ("rh", "rl"):
        try: c.execute(f"ALTER TABLE pivot_signals ADD COLUMN {col} REAL")                    # migration for a log created before the range columns existed
        except sqlite3.OperationalError: pass
    return c


def five_minute(bars: dict, now_epoch: float) -> pd.DataFrame:
    """1-minute bars -> COMPLETE 5-minute bars only (a bin is used once its last minute has closed)."""
    if len(bars["t"]) == 0: return pd.DataFrame(columns=["t", "o", "h", "l", "c", "v"])
    d = pd.DataFrame({"t": pd.to_datetime(bars["t"], unit="s", utc=True), "o": bars["o"], "h": bars["h"], "l": bars["l"], "c": bars["c"]}); d["bin"] = d["t"].dt.floor("5min")
    g = d.groupby("bin").agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), n=("o", "size"), last=("t", "max")).reset_index()
    g = g[((g["bin"] + pd.Timedelta(minutes=5)) - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds() <= now_epoch]            # unit-safe (pandas 3 datetimes are not always ns); g = g[g["n"] >= 5]
    return g.rename(columns={"bin": "t"})[["t", "o", "h", "l", "c"]].assign(v=0.0).reset_index(drop=True)


def _fill_forward(c, df: pd.DataFrame, symbol: str):
    rows = c.execute("SELECT id, bar_start, side, fwd3, fwd6, fwd12 FROM pivot_signals WHERE symbol=? AND (fwd3 IS NULL OR fwd6 IS NULL OR fwd12 IS NULL)", (symbol,)).fetchall()
    if not rows or df.empty: return
    idx = {pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ"): i for i, t in enumerate(df["t"])}; o = df["o"].values; cl = df["c"].values; day = df["t"].dt.tz_convert("Asia/Kolkata").dt.date.values
    for rid, bs, side, f3, f6, f12 in rows:
        i = idx.get(bs)
        if i is None: continue
        sg = 1.0 if side == "BUY" else -1.0; upd = {}
        for h, cur in zip(HORIZONS, (f3, f6, f12)):
            if cur is None and i + h < len(df) and day[i + h] == day[i] and i + 1 < len(df): upd[f"fwd{h}"] = round(sg * (cl[i + h] / o[i + 1] - 1) * 1e4, 2)
        if upd: c.execute("UPDATE pivot_signals SET " + ", ".join(f"{k}=?" for k in upd) + " WHERE id=?", (*upd.values(), rid))


def observe(symbol: str, now_epoch: float | None = None, bars_provider=None, db_path: str | None = None, send=None) -> list[dict]:
    """Evaluate the last complete 5-minute bar of an index; log + (optionally) announce new signals. Returns the new signals."""
    sym = symbol.upper()
    if sym not in SYMBOLS: return []
    now = now_epoch or time.time(); bars = (bars_provider or _rsh.recent_bars)(sym, "INDEX", now, 6)
    df = five_minute(bars, now)
    if len(df) < 40: return []
    key = (sym, str(df["t"].iloc[-1]))
    with _lock:
        fresh = _last_bar.get(sym) != key; _last_bar[sym] = key
    new = []
    with _conn(db_path) as c:
        _fill_forward(c, df, sym)
        if not fresh: return []
        from ..candle_ai import pivot_reversal as PR, range_zone as RZ
        sg = PR.signals(df); last = sg.iloc[-1]; bs = pd.Timestamp(df["t"].iloc[-1]).strftime("%Y-%m-%dT%H:%M:%SZ"); rz = RZ.signals(df); rl_ = rz.iloc[-1]
        fired = [(v, None, None) for v in PR.VARIANTS if bool(last[v])] + [("RZ_" + v, float(rl_["rh"]), float(rl_["rl"])) for v in RZ.VARIANTS if bool(rl_[v])]
        for v, rh_, rl2 in fired:
            if c.execute("INSERT OR IGNORE INTO pivot_signals (symbol, bar_start, variant, side, close, logged_ts, rh, rl) VALUES (?,?,?,?,?,?,?,?)", (sym, bs, v, SIDE[v], float(df["c"].iloc[-1]), now, rh_, rl2)).rowcount:
                new.append({"symbol": sym, "bar_start": bs, "variant": v, "side": SIDE[v], "close": float(df["c"].iloc[-1]), "rh": rh_, "rl": rl2})
    if new and (send is not None or os.environ.get("CHANAKYA_PIVOT_SHADOW_TG", "1") not in ("0", "false", "no")):
        try:
            def _line(n):
                if n["variant"].startswith("RZ_"): return f"{n['symbol']} {n['side']} ({n['variant'][3:].replace('_', ' ').title()}) range {n['rl']:.2f}-{n['rh']:.2f}, 5m close {n['close']:.2f}"
                return f"{n['symbol']} {n['side']} ({n['variant'].replace('_', ' ').title()}) at pivot line after consolidation, 5m close {n['close']:.2f}"
            msg = "Pattern/range signal (observation only)\n" + "\n".join(_line(n) for n in new) + "\nHistorical test: no demonstrated edge after costs. Not an order."
            if send: send(msg)
            else:
                from ..connectors import telegram
                telegram._send(msg, os.environ.get("TELEGRAM_CHAT_ID"))
        except Exception as e: _log.debug("pivot shadow telegram failed (cosmetic): %r", e)
    return new


def observe_async(symbol: str) -> None:
    if not enabled(): return
    def _run():
        try: observe(symbol)
        except Exception as e: _log.debug("pivot shadow observe failed for %s (cosmetic): %r", symbol, e)
    threading.Thread(target=_run, name="pivot-shadow", daemon=True).start()


def current_state(symbol: str, now_epoch: float | None = None, bars_provider=None) -> dict:
    """The live range / zone picture of an index: latest 5m box (if one exists), its zones, where price is, plus the last fired signals."""
    from ..candle_ai import range_zone as RZ
    sym = symbol.upper(); now = now_epoch or time.time()
    df = five_minute((bars_provider or _rsh.recent_bars)(sym, "INDEX", now, 6), now)
    if len(df) < 40: return {"symbol": sym, "status": "NO_DATA"}
    b = RZ.box(df); r = b.iloc[-1]; last = float(df["c"].iloc[-1]); atr = float(r["atr"]) if np.isfinite(r["atr"]) else None
    if not bool(r["valid"]): return {"symbol": sym, "status": "NO_RANGE", "price": last, "bar": str(df["t"].iloc[-1])}
    z = RZ.EDGE_ATR * atr; rh, rl = float(r["rh"]), float(r["rl"]); pos = "ABOVE_RANGE" if last > rh else "BELOW_RANGE" if last < rl else ("UPPER_ZONE" if last >= rh - z else "LOWER_ZONE" if last <= rl + z else "INSIDE")
    return {"symbol": sym, "status": "RANGE", "range_high": rh, "range_low": rl, "mid": float(r["mid"]), "upper_zone": [rh - z, rh + z], "lower_zone": [rl - z, rl + z], "price": last, "position": pos, "atr": atr, "bar": str(df["t"].iloc[-1])}
