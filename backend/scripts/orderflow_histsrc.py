#!/usr/bin/env python3
"""
orderflow_histsrc.py -- READ-ONLY historical data access for Stage-3.

Unifies two sources, all opened read-only, nothing mutated:
  * /root/oi_dashboard/oi_history.db
      - live_candles  : 5m OHLC (volume is 0 in this feed) -- 2026-08-14..28
      - cycles+strikes: ~9-20s option chain (CE/PE LTP, OI, IV, greeks) and
                        underlying_ltp -- 2026-07-13..2026-08-28
  * zerohero market_hub (histcap)  -- 2026-09-01..04

Provides a market_hub-shaped slice for the order-flow research scripts:
  sessions(sym)              -> [(date_str, bar_src)]  chronological
  session_bars(sym, date)    -> [{"bar_start","o","h","l","c","v"}]  5m
  session_oi_series(sym,date)-> {(strike,"CE"/"PE"): [(ts, ltp, oi, delta), ...]}

Timestamps are kept in each session's NATIVE format (oi_dashboard sessions:
IST-naive 'YYYY-MM-DDTHH:MM:SS[.ffffff]'; zerohero: '...Z' UTC). Bars and the
option series inside one session always share a format, so lexical as-of
comparison stays valid. No cross-source timestamp mixing within a session.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OI_DB = "/root/oi_dashboard/oi_history.db"
SYMS = ("NIFTY", "NATURALGAS", "CRUDEOIL")
_RTH = {  # IST minutes-of-day inclusive; MCX runs late
    "NIFTY": (555, 930), "NATURALGAS": (540, 1425), "CRUDEOIL": (540, 1425),
}


def _ro(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


# --------------------------------------------------------------- oi_dashboard
def _od_dates(sym):
    with _ro(OI_DB) as c:
        lc = {r[0] for r in c.execute(
            "SELECT DISTINCT substr(datetime,1,10) FROM live_candles "
            "WHERE symbol=? AND timeframe='5m'", (sym,))}
        cy = {r[0] for r in c.execute(
            "SELECT DISTINCT date FROM cycles WHERE symbol=?", (sym,))}
    return lc, cy


def _od_bars_livecandles(sym, date):
    with _ro(OI_DB) as c:
        rows = c.execute(
            "SELECT datetime, open, high, low, close, volume FROM live_candles "
            "WHERE symbol=? AND timeframe='5m' AND substr(datetime,1,10)=? "
            "ORDER BY datetime", (sym, date)).fetchall()
    lo, hi = _RTH.get(sym, (0, 1440))
    out = []
    for r in rows:
        t = datetime.fromisoformat(r["datetime"])
        if not (lo <= t.hour * 60 + t.minute <= hi):
            continue
        if r["open"] is None or r["high"] is None:
            continue
        out.append({"bar_start": r["datetime"], "o": r["open"], "h": r["high"],
                    "l": r["low"], "c": r["close"], "v": 0.0})
    return out


def _od_bars_resampled(sym, date, tf_min=5):
    """5m OHLC resampled from cycles.underlying_ltp (~9-20s cadence)."""
    with _ro(OI_DB) as c:
        rows = c.execute(
            "SELECT ts, underlying_ltp FROM cycles WHERE symbol=? AND date=? "
            "AND underlying_ltp IS NOT NULL ORDER BY ts", (sym, date)).fetchall()
    lo, hi = _RTH.get(sym, (0, 1440))
    buckets: dict = {}
    for r in rows:
        t = datetime.fromisoformat(r["ts"])
        if not (lo <= t.hour * 60 + t.minute <= hi):
            continue
        k = t.replace(minute=(t.minute // tf_min) * tf_min, second=0, microsecond=0)
        b = buckets.setdefault(k, [])
        b.append(r["underlying_ltp"])
    out = []
    for k in sorted(buckets):
        v = buckets[k]
        out.append({"bar_start": k.isoformat(), "o": v[0], "h": max(v),
                    "l": min(v), "c": v[-1], "v": 0.0})
    return out


def _od_oi_series(sym, date):
    with _ro(OI_DB) as c:
        rows = c.execute(
            "SELECT c.ts, s.strike, s.ce_ltp, s.pe_ltp, s.ce_oi, s.pe_oi, "
            "       s.ce_delta, s.pe_delta, s.ce_vol, s.pe_vol "
            "FROM cycles c JOIN strikes s ON s.cycle_id=c.id "
            "WHERE c.symbol=? AND c.date=? ORDER BY c.ts", (sym, date)).fetchall()
    out: dict = {}
    for r in rows:
        try:
            k = float(r["strike"])
        except (TypeError, ValueError):
            continue
        # tuple: (ts, ltp, oi, delta, cum_vol)  -- ce_vol/pe_vol are cumulative
        # session traded volume for that strike; difference consecutive rows for
        # per-interval option transaction volume (Level-2 proxy, not aggressor delta).
        if r["ce_ltp"] is not None:
            out.setdefault((k, "CE"), []).append((r["ts"], r["ce_ltp"], r["ce_oi"], r["ce_delta"], r["ce_vol"]))
        if r["pe_ltp"] is not None:
            out.setdefault((k, "PE"), []).append((r["ts"], r["pe_ltp"], r["pe_oi"], r["pe_delta"], r["pe_vol"]))
    return out


# --------------------------------------------------------------- unified API
def sessions(sym: str):
    sym = sym.upper()
    lc, cy = _od_dates(sym)
    out = []
    for d in sorted(cy):
        out.append((d, "live_candles" if d in lc else "cycles_resampled"))
    # zerohero histcap (Sep)
    try:
        from app import market_hub
        for d in sorted(market_hub.session_dates(sym, limit=60)):
            if d > (out[-1][0] if out else "0"):
                out.append((d, "zerohero"))
    except Exception:
        pass
    return out


def session_bars(sym: str, date: str, src: str = None):
    sym = sym.upper()
    if src is None:
        src = dict(sessions(sym)).get(date, "cycles_resampled")
    if src == "zerohero":
        from app import market_hub
        return market_hub.session_bars(sym, date)
    if src == "live_candles":
        b = _od_bars_livecandles(sym, date)
        if b:
            return b
    return _od_bars_resampled(sym, date)


def session_oi_series(sym: str, date: str, src: str = None):
    """{(strike, 'CE'/'PE'): [(ts, ltp, oi, delta), ...]} oldest-first."""
    sym = sym.upper()
    if src is None:
        src = dict(sessions(sym)).get(date, "cycles_resampled")
    if src == "zerohero":
        from app import market_hub
        raw = market_hub.session_option_quotes(sym, date)  # {(strike,side): [(ts,ltp)]}
        return {k: [(t, v, None, None, None) for t, v in ser] for k, ser in raw.items()}
    return _od_oi_series(sym, date)


if __name__ == "__main__":
    for s in SYMS:
        ss = sessions(s)
        by_src = {}
        for _, src in ss:
            by_src[src] = by_src.get(src, 0) + 1
        print(f"{s:<11} {len(ss)} sessions  {by_src}  [{ss[0][0]}..{ss[-1][0]}]")
        d0 = ss[0][0]
        b = session_bars(s, d0, ss[0][1])
        oi = session_oi_series(s, d0, ss[0][1])
        print(f"           first session {d0} ({ss[0][1]}): {len(b)} bars, "
              f"{len(oi)} option legs, "
              f"strikes {sorted({k[0] for k in oi})[:3]}..{sorted({k[0] for k in oi})[-2:]}")
