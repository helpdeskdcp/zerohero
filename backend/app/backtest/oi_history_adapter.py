"""
Read-only adapter over the BATI / oi_dashboard historical database
(`/root/oi_dashboard/oi_history.db`).

It NEVER writes to, migrates, or copies the source DB. It converts the two
core historical tables into `/root/zerohero`'s canonical market-data shapes so
the S/R engine, 4-state classifier, MTF / regime engines, CE/PE engines,
option-quality selector, probability calibration and the replay/backtest
harness can all consume one format regardless of whether the source is a live
WebSocket feed or this archive.

Discovered source schema (inspected 2026-08-30, do not assume — see
`data_quality_manifest()`):

  cycles(id, symbol, ts, date, time, underlying_ltp, atm, pcr, max_pain,
         bias, note, signal_*)                       -- 466k rows, 2026-07-13..08-28
  strikes(cycle_id, strike, ce_oi, ce_oi_chg, ce_vol, ce_ltp, ce_chg_pct,
          ce_signal, pe_* , ce_iv, pe_iv, ce_delta.., ce_token, pe_token,
          ce_trading_symbol, ce_contract_expiry, ...) -- 4.2M rows, ATM +/-4

Key adaptations (source limitations -> what this adapter does):
  * `ce_vol` / `pe_vol` are CUMULATIVE intraday -> emitted BOTH as `vol_cum`
    and as `vol_delta` (per-cycle difference, reset at the day boundary and
    on any decrease). Negative deltas are clamped to 0.
  * greeks are ~40% NULL, tokens / contract-expiry ~90% NULL -> passed through
    as `None`; never fabricated.
  * no 15m/30m candles in the source and index `live_candles.volume` is 85%
    zero -> `resample_candles()` synthesises index/option OHLC from the tick
    stream (`cycles.underlying_ltp` / `strikes.*_ltp`) at any timeframe, with
    the index-candle volume proxied from ATM CE+PE `vol_delta`.
  * every canonical record carries `_src` provenance back to `cycles.id`.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from itertools import groupby
from pathlib import Path
from typing import Iterator, Optional

DEFAULT_DB = "/root/oi_dashboard/oi_history.db"

# Timeframes this adapter can synthesise, in minutes.
_TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}

# strike-level greek columns, emitted as None when the source row has NULL.
_CE_GREEKS = ("ce_iv", "ce_delta", "ce_gamma", "ce_theta", "ce_vega")
_PE_GREEKS = ("pe_iv", "pe_delta", "pe_gamma", "pe_theta", "pe_vega")


def db_path() -> str:
    return os.environ.get("OI_HISTORY_DB", DEFAULT_DB)


def is_available() -> bool:
    p = db_path()
    return bool(p) and Path(p).is_file() and Path(p).stat().st_size > 0


def _connect() -> sqlite3.Connection:
    p = db_path()
    if not Path(p).is_file():
        raise FileNotFoundError(f"oi_history DB not found: {p} (set OI_HISTORY_DB)")
    # immutable=1 => the file will not be modified by anyone while open; gives a
    # fast, lock-free read and guarantees this process cannot write it.
    conn = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _f(v):
    """float or None (never fabricate a 0 for a missing value)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _minute_of_day(ts: str) -> Optional[int]:
    """IST minute-of-day from a naive 'YYYY-MM-DDTHH:MM:SS[.ffffff]' string.
    The source stores IST wall-clock with no timezone."""
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt.hour * 60 + dt.minute


def _bucket_start(ts: str, tf_min: int) -> Optional[str]:
    """Floor a timestamp to the start of its `tf_min` bucket (IST wall-clock),
    returned as an ISO 'YYYY-MM-DDTHH:MM:00' string."""
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    m = dt.hour * 60 + dt.minute
    floored = (m // tf_min) * tf_min
    return f"{dt.date().isoformat()}T{floored // 60:02d}:{floored % 60:02d}:00"


# --------------------------------------------------------------------------- #
#  Canonical market-state stream                                             #
# --------------------------------------------------------------------------- #
_STATE_SQL = """
SELECT c.id AS cycle_id, c.ts, c.date, c.underlying_ltp, c.atm, c.pcr,
       c.max_pain, c.bias,
       s.strike,
       s.ce_oi, s.ce_oi_chg, s.ce_vol, s.ce_ltp, s.ce_chg_pct,
       s.pe_oi, s.pe_oi_chg, s.pe_vol, s.pe_ltp, s.pe_chg_pct,
       s.ce_iv, s.ce_delta, s.ce_gamma, s.ce_theta, s.ce_vega,
       s.pe_iv, s.pe_delta, s.pe_gamma, s.pe_theta, s.pe_vega,
       s.ce_token, s.pe_token, s.ce_trading_symbol, s.pe_trading_symbol,
       s.ce_contract_expiry, s.pe_contract_expiry
FROM cycles c
JOIN strikes s ON s.cycle_id = c.id
WHERE c.symbol = ?
  {date_clause}
ORDER BY c.ts, s.strike
"""


def iter_market_states(symbol: str, start: str | None = None,
                       end: str | None = None) -> Iterator[dict]:
    """Yield one canonical market-state per cycle, strictly in chronological
    `cycles.ts` order.  `start` / `end` are inclusive `YYYY-MM-DD` date bounds.

    Each yielded dict:
        {ts, symbol, index_ltp, atm, pcr, max_pain, bias,
         chain: [{strike, ce:{ltp,oi,oi_chg,vol_cum,vol_delta,chg_pct,
                               iv,delta,gamma,theta,vega,token,tradingsymbol,expiry},
                  pe:{...}}, ...],
         _src: {"cycle_id": int, "db": <path>}}

    No look-ahead: the generator only ever reads rows already ordered by ts.
    """
    symbol = str(symbol or "").upper()
    clause = ""
    params: list = [symbol]
    if start:
        clause += " AND c.date >= ?"
        params.append(start)
    if end:
        clause += " AND c.date <= ?"
        params.append(end)
    sql = _STATE_SQL.format(date_clause=clause)

    prev_vol: dict[int, tuple[float, float]] = {}   # strike -> (ce_vol, pe_vol)
    cur_date: str | None = None

    conn = _connect()
    try:
        cur = conn.execute(sql, params)
        for cycle_id, rows in groupby(cur, key=lambda r: r["cycle_id"]):
            rows = list(rows)
            head = rows[0]
            if head["date"] != cur_date:
                prev_vol.clear()          # cumulative volume resets each day
                cur_date = head["date"]

            chain = []
            for r in rows:
                strike = r["strike"]
                ce_vol = _f(r["ce_vol"]) or 0.0
                pe_vol = _f(r["pe_vol"]) or 0.0
                p_ce, p_pe = prev_vol.get(strike, (None, None))
                ce_delta_vol = 0.0 if p_ce is None or ce_vol < p_ce else ce_vol - p_ce
                pe_delta_vol = 0.0 if p_pe is None or pe_vol < p_pe else pe_vol - p_pe
                prev_vol[strike] = (ce_vol, pe_vol)

                chain.append({
                    "strike": strike,
                    "ce": {
                        "ltp": _f(r["ce_ltp"]), "oi": _f(r["ce_oi"]),
                        "oi_chg": _f(r["ce_oi_chg"]), "vol_cum": ce_vol,
                        "vol_delta": ce_delta_vol, "chg_pct": _f(r["ce_chg_pct"]),
                        "iv": _f(r["ce_iv"]), "delta": _f(r["ce_delta"]),
                        "gamma": _f(r["ce_gamma"]), "theta": _f(r["ce_theta"]),
                        "vega": _f(r["ce_vega"]),
                        "token": r["ce_token"] or None,
                        "tradingsymbol": r["ce_trading_symbol"] or None,
                        "expiry": r["ce_contract_expiry"] or None,
                    },
                    "pe": {
                        "ltp": _f(r["pe_ltp"]), "oi": _f(r["pe_oi"]),
                        "oi_chg": _f(r["pe_oi_chg"]), "vol_cum": pe_vol,
                        "vol_delta": pe_delta_vol, "chg_pct": _f(r["pe_chg_pct"]),
                        "iv": _f(r["pe_iv"]), "delta": _f(r["pe_delta"]),
                        "gamma": _f(r["pe_gamma"]), "theta": _f(r["pe_theta"]),
                        "vega": _f(r["pe_vega"]),
                        "token": r["pe_token"] or None,
                        "tradingsymbol": r["pe_trading_symbol"] or None,
                        "expiry": r["pe_contract_expiry"] or None,
                    },
                })

            yield {
                "ts": head["ts"],
                "symbol": symbol,
                "index_ltp": _f(head["underlying_ltp"]),
                "atm": head["atm"],
                "pcr": _f(head["pcr"]),
                "max_pain": head["max_pain"],
                "bias": head["bias"],
                "chain": chain,
                "_src": {"cycle_id": cycle_id, "db": db_path()},
            }
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
#  Synthetic OHLC (index or a specific option strike)                        #
# --------------------------------------------------------------------------- #
def resample_candles(symbol: str, tf: str, *, kind: str = "index",
                     strike: int | None = None, option_type: str = "CE",
                     start: str | None = None, end: str | None = None) -> dict:
    """Build `{t,o,h,l,c,v}` candles from the tick stream.

    kind="index"  -> price = cycles.underlying_ltp; volume = sum of ATM
                     (CE vol_delta + PE vol_delta) in the bucket (proxy).
    kind="option" -> price = strikes.<ot>_ltp for `strike`; volume = that
                     leg's vol_delta sum.  `strike` is required.

    Returns {"symbol","timeframe","kind","source":"OI_HISTORY","candles":[...],
             "count", "_src":{"cycle_id_min","cycle_id_max"}}.
    Bars are ordered oldest->newest.  No look-ahead: each bar is closed only
    from ticks whose ts falls inside it.
    """
    symbol = str(symbol or "").upper()
    tf_min = _TF_MIN.get(str(tf).lower())
    if tf_min is None:
        raise ValueError(f"unsupported timeframe {tf!r}; use one of {sorted(_TF_MIN)}")
    ot = str(option_type or "CE").upper()
    if kind == "option" and (strike is None or ot not in ("CE", "PE")):
        raise ValueError("kind='option' requires strike and option_type CE|PE")

    where = ["c.symbol = ?"]
    params: list = [symbol]
    if start:
        where.append("c.date >= ?"); params.append(start)
    if end:
        where.append("c.date <= ?"); params.append(end)

    if kind == "index":
        sql = (
            "SELECT c.id, c.ts, c.underlying_ltp AS px, "
            "       COALESCE(s.ce_vol,0) AS cev, COALESCE(s.pe_vol,0) AS pev, "
            "       s.strike AS s_strike, c.date AS d "
            "FROM cycles c LEFT JOIN strikes s "
            "  ON s.cycle_id = c.id AND s.strike = c.atm "
            f"WHERE {' AND '.join(where)} ORDER BY c.ts"
        )
    else:
        where.append("s.strike = ?"); params.append(int(strike))
        col = "ce_ltp" if ot == "CE" else "pe_ltp"
        vcol = "ce_vol" if ot == "CE" else "pe_vol"
        sql = (
            f"SELECT c.id, c.ts, s.{col} AS px, COALESCE(s.{vcol},0) AS cev, "
            f"       0 AS pev, s.strike AS s_strike, c.date AS d "
            "FROM cycles c JOIN strikes s ON s.cycle_id = c.id "
            f"WHERE {' AND '.join(where)} ORDER BY c.ts"
        )

    bars: list[dict] = []
    cur_bucket: str | None = None
    cid_min = cid_max = None
    prev_cev: dict[int, float] = {}
    prev_pev: dict[int, float] = {}
    cur_date: str | None = None

    conn = _connect()
    try:
        for r in conn.execute(sql, params):
            px = _f(r["px"])
            if px is None:
                continue
            ts = r["ts"]
            bucket = _bucket_start(ts, tf_min)
            if bucket is None:
                continue

            if r["d"] != cur_date:
                prev_cev.clear(); prev_pev.clear(); cur_date = r["d"]
            sk = r["s_strike"]
            cev, pev = _f(r["cev"]) or 0.0, _f(r["pev"]) or 0.0
            pc, pp = prev_cev.get(sk), prev_pev.get(sk)
            dv = 0.0
            if pc is not None and cev >= pc:
                dv += cev - pc
            if pp is not None and pev >= pp:
                dv += pev - pp
            prev_cev[sk], prev_pev[sk] = cev, pev

            cid = r["id"]
            cid_min = cid if cid_min is None else min(cid_min, cid)
            cid_max = cid if cid_max is None else max(cid_max, cid)

            if bucket != cur_bucket:
                bars.append({"t": bucket, "o": px, "h": px, "l": px, "c": px, "v": 0.0})
                cur_bucket = bucket
            b = bars[-1]
            b["h"] = max(b["h"], px)
            b["l"] = min(b["l"], px)
            b["c"] = px
            b["v"] += dv
    finally:
        conn.close()

    return {
        "symbol": symbol, "timeframe": tf, "kind": kind,
        "strike": strike if kind == "option" else None,
        "option_type": ot if kind == "option" else None,
        "source": "OI_HISTORY", "candles": bars, "count": len(bars),
        "_src": {"cycle_id_min": cid_min, "cycle_id_max": cid_max, "db": db_path()},
    }


# --------------------------------------------------------------------------- #
#  Coverage / quality manifest                                              #
# --------------------------------------------------------------------------- #
def data_quality_manifest(symbol: str | None = None) -> dict:
    """Machine-readable coverage report so every backtest result can be
    annotated with the exact limitations it ran under. Cheap aggregate queries
    only; safe to call before a run."""
    if not is_available():
        return {"available": False, "db": db_path(), "reason": "oi_history DB not found"}
    conn = _connect()
    try:
        out: dict = {"available": True, "db": db_path()}
        row = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM cycles"
            + (" WHERE symbol=?" if symbol else ""),
            ([symbol.upper()] if symbol else []),
        ).fetchone()
        out["cycles"] = {"date_min": row[0], "date_max": row[1], "rows": row[2]}
        out["symbols"] = [
            {"symbol": r[0], "cycles": r[1], "days": r[2], "date_min": r[3], "date_max": r[4]}
            for r in conn.execute(
                "SELECT symbol, COUNT(*), COUNT(DISTINCT date), MIN(date), MAX(date) "
                "FROM cycles GROUP BY symbol ORDER BY COUNT(*) DESC")
        ]
        s = conn.execute(
            "SELECT COUNT(*), "
            "SUM(ce_ltp IS NULL OR pe_ltp IS NULL), "
            "SUM(ce_oi IS NULL OR pe_oi IS NULL), "
            "SUM(ce_iv IS NULL), "
            "SUM(ce_token IS NULL OR ce_token=''), "
            "SUM(ce_contract_expiry IS NULL OR ce_contract_expiry='') "
            "FROM strikes").fetchone()
        n = s[0] or 1
        out["strikes"] = {
            "rows": s[0],
            "null_ltp_pct": round(100 * (s[1] or 0) / n, 2),
            "null_oi_pct": round(100 * (s[2] or 0) / n, 2),
            "null_greeks_pct": round(100 * (s[3] or 0) / n, 2),
            "null_token_pct": round(100 * (s[4] or 0) / n, 2),
            "null_expiry_pct": round(100 * (s[5] or 0) / n, 2),
        }
        out["known_limitations"] = [
            "candles are synthesised from cycles.underlying_ltp / strikes.*_ltp "
            "(source live_candles has only 1m/3m/5m and ~85% zero index volume)",
            "option volume is cumulative intraday -> vol_delta is a per-cycle "
            "difference, reset daily; treat as approximate",
            "greeks NULL ~40% of rows; tokens/contract-expiry NULL ~90% "
            "(present only for the most recent data)",
            "poll cadence changed: ~7-11s (2026-07-13..08-07) then ~45s "
            "(2026-08-10..08-28); segment backtests accordingly",
            "chain is ATM +/-4 strikes only",
        ]
        return out
    finally:
        conn.close()
