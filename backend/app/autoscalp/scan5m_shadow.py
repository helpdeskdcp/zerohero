"""Owner's 5-minute bullish / bearish 'balanced' scans on the front-month futures, as an OBSERVATION-ONLY feed (rule + notes: data/research/scanner/SPEC_5M_BULLISH_2026-10-24.md,
code: app/scanner/intraday.py). Own SQLite file, daemon thread, never an input to any autoscalp decision. A hit is a candidate for the owner's own CE / PE analysis, not a trade:
the back-test (80 sessions of futures, 6 instruments) found NO edge (FULL variants gross about 0 bp at 15-60 minutes) and the messages say so. 'Liquidity sweep' is a previous-bar high/low proxy;
no order flow is involved. Kill switches: CHANAKYA_SCAN5M_SHADOW=0 (all), CHANAKYA_SCAN5M_TG=0 (Telegram only)."""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time

import numpy as np
import pandas as pd

from ..scanner import intraday as I

_log = logging.getLogger("autoscalp.scan5m_shadow")
HORIZONS = (3, 6, 12)
COOLDOWN_SEC = 1800
_DB = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "scan5m_shadow.db"))
_SCHEMA = """CREATE TABLE IF NOT EXISTS scan5m_hits (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, bar_start TEXT NOT NULL, direction TEXT NOT NULL, grp TEXT NOT NULL, close REAL, rsi REAL, vol_ratio REAL,
  logged_ts REAL NOT NULL, fwd3 REAL, fwd6 REAL, fwd12 REAL, UNIQUE (symbol, bar_start, direction));"""
_last_bin: dict = {}
_last_tg: dict = {}
_lock = threading.Lock()


def enabled() -> bool:
    if os.environ.get("CHANAKYA_SCAN5M_SHADOW", "1") in ("0", "false", "no"): return False
    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get("CHANAKYA_SCAN5M_DB"): return False
    return True


def _db() -> str: return os.environ.get("CHANAKYA_SCAN5M_DB", _DB)


def _conn(path=None):
    c = sqlite3.connect(path or _db(), timeout=5); c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=5000"); c.executescript(_SCHEMA); return c


def _fill_forward(c, df: pd.DataFrame, sym: str):
    rows = c.execute("SELECT id, bar_start, direction, fwd3, fwd6, fwd12 FROM scan5m_hits WHERE symbol=? AND (fwd3 IS NULL OR fwd6 IS NULL OR fwd12 IS NULL)", (sym,)).fetchall()
    if not rows or df.empty: return
    idx = {pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ"): i for i, t in enumerate(df["t"])}; o = df["o"].values; cl = df["c"].values; day = df["t"].dt.tz_convert("Asia/Kolkata").dt.date.values
    for rid, bs, direction, f3, f6, f12 in rows:
        i = idx.get(bs)
        if i is None: continue
        sg = 1.0 if direction.startswith("BULL") else -1.0; upd = {}
        for h, cur in zip(HORIZONS, (f3, f6, f12)):
            if cur is None and i + 1 < len(df) and i + h < len(df) and day[i + h] == day[i]: upd[f"fwd{h}"] = round(sg * (cl[i + h] / o[i + 1] - 1) * 1e4, 2)
        if upd: c.execute("UPDATE scan5m_hits SET " + ", ".join(f"{k}=?" for k in upd) + " WHERE id=?", (*upd.values(), rid))


def observe(symbol: str, now_epoch: float | None = None, frame_provider=None, db_path: str | None = None, send=None) -> list[dict]:
    sym = symbol.upper()
    if sym not in I.INSTRUMENTS: return []
    now = now_epoch or time.time(); cur_bin = int(now // 300)
    with _lock:
        if _last_bin.get(sym) == cur_bin: return []
        _last_bin[sym] = cur_bin
    df = (frame_provider or (lambda s, until: I.futures_5m(s, since_epoch=until - 6 * 86400, until_epoch=until)))(sym, now)
    if len(df) < 60: return []
    f = I.scan_flags(df); last = f.iloc[-1]; bs = pd.Timestamp(df["t"].iloc[-1]).strftime("%Y-%m-%dT%H:%M:%SZ"); new = []
    with _conn(db_path) as c:
        _fill_forward(c, df, sym)
        for direction, pre in (("BULL", ""), ("BEAR", ""), ("BULL_SW", "SW_"), ("BEAR_SW", "SW_")):
            side = direction.split("_")[0]
            if bool(last[f"{pre}FULL_{side}"]):
                grp = ("A" if bool(last[f"{pre}A_{side}"]) else "") + ("B" if bool(last[f"{pre}B_{side}"]) else "")
                if c.execute("INSERT OR IGNORE INTO scan5m_hits (symbol, bar_start, direction, grp, close, rsi, vol_ratio, logged_ts) VALUES (?,?,?,?,?,?,?,?)", (sym, bs, direction, grp, float(df["c"].iloc[-1]), float(last["rsi"]), float(last["vol_ratio"]), now)).rowcount:
                    new.append({"symbol": sym, "bar_start": bs, "direction": direction, "grp": grp, "close": float(df["c"].iloc[-1]), "rsi": float(last["rsi"]), "vol_ratio": float(last["vol_ratio"])})
    for n in new:
        if n["direction"].endswith("_SW"): continue                                                           # swing variants are logged for the forward record only, no message
        key = (n["symbol"], n["direction"])
        if now - _last_tg.get(key, 0) < COOLDOWN_SEC: continue
        _last_tg[key] = now
        if send is not None or os.environ.get("CHANAKYA_SCAN5M_TG", "1") not in ("0", "false", "no"):
            try:
                kind = {"A": "sweep/reversal", "B": "breakout" if n["direction"] == "BULL" else "breakdown", "AB": "sweep + break"}.get(n["grp"], n["grp"])
                msg = f"5m {'BULLISH' if n['direction'] == 'BULL' else 'BEARISH'} scan (observation only): {n['symbol']} FUT {kind}, close {n['close']:.2f}, RSI {n['rsi']:.0f}, volume x{n['vol_ratio']:.1f}\nCandidate for your {'CE' if n['direction'] == 'BULL' else 'PE'} analysis, not a trade. Back-test: gross edge about 0; confirm in paper first."
                if send: send(msg)
                else:
                    from ..connectors import telegram
                    telegram._send(msg, os.environ.get("TELEGRAM_CHAT_ID"))
            except Exception as e: _log.debug("scan5m telegram failed (cosmetic): %r", e)
    return new


def observe_async(symbol: str) -> None:
    if not enabled(): return
    def _run():
        try: observe(symbol)
        except Exception as e: _log.debug("scan5m shadow failed for %s (cosmetic): %r", symbol, e)
    threading.Thread(target=_run, name="scan5m-shadow", daemon=True).start()


def report(db_path: str | None = None, limit: int = 40) -> dict:
    p = db_path or _db()
    if not os.path.exists(p): return {"latest": [], "forward": {}}
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    try:
        d = pd.read_sql_query("SELECT symbol, bar_start, direction, grp, close, rsi, vol_ratio, fwd3, fwd6, fwd12 FROM scan5m_hits ORDER BY id DESC", c)
    finally:
        c.close()
    fw = {}
    for dr, g in d.groupby("direction"):
        fw[dr] = {"hits": int(len(g)), **{f"mean_fwd{h}_bp_gross": (round(float(g[f"fwd{h}"].mean()), 2) if g[f"fwd{h}"].notna().sum() >= 10 else None) for h in HORIZONS}, **{f"n_fwd{h}": int(g[f"fwd{h}"].notna().sum()) for h in HORIZONS}}
    return {"latest": d.head(limit).to_dict("records"), "forward": fw, "note": "forward returns are gross bps in the signal direction (BEAR = short); back-test found no edge; paper / analysis only"}
