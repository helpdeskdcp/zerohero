"""
Read NIFTY (or BANKNIFTY) option OI + LTP series straight from the histcap
capture DB (data/market_history.db, quote_snapshots).

This is the ONLY place real per-strike OPTION OPEN INTEREST exists for this
project: AngelOne's historical candle API has no OI, and expired weekly
contracts are purged from the master. histcap stores AngelOne `opnInterest`
(ACTUAL) every ~20-30s during market hours. ΔOI is DERIVED by differencing.

NOTE: histcap only has NIFTY 08SEP2026 so far, and 08SEP has NOT expired — so
this yields a NON-EXPIRY-DAY session. Useful to test the OI lead/lag question
(H4/H5) on real data; not a Zero-to-Hero expiry-day validation case.
"""
from __future__ import annotations

import os
import sqlite3

try:
    from ..histcap.store import DB_PATH as _HIST_DB
except Exception:                                   # pragma: no cover
    _HIST_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "data", "market_history.db")


def _ro(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def available_sessions(symbol="NIFTY") -> list[dict]:
    with _ro(_HIST_DB) as c:
        return [dict(r) for r in c.execute(
            "SELECT session_date_ist AS date, expiry, COUNT(*) AS snaps, "
            "COUNT(DISTINCT strike) AS strikes, MIN(received_ts) t0, MAX(received_ts) t1 "
            "FROM quote_snapshots WHERE symbol=? AND kind='OPTION' AND oi IS NOT NULL "
            "GROUP BY session_date_ist, expiry ORDER BY session_date_ist", (symbol.upper(),)).fetchall()]


def load_oi_premium(symbol, session_date, expiry, *, atm_hint=None,
                    n_each_side=3, grid_sec=60):
    """Return {atm, step, grid_minutes:[...], strikes:{strike: {ce:{oi:[],ltp:[]},
    pe:{oi:[],ltp:[]}}}} sampled onto a `grid_sec` grid (default 1 min) by
    last-value-carried-forward. All OI = ACTUAL; caller derives ΔOI."""
    with _ro(_HIST_DB) as c:
        rows = c.execute(
            "SELECT received_ts, strike, option_type, oi, ltp FROM quote_snapshots "
            "WHERE symbol=? AND kind='OPTION' AND session_date_ist=? AND expiry=? "
            "AND strike IS NOT NULL ORDER BY received_ts",
            (symbol.upper(), session_date, expiry)).fetchall()
    if not rows:
        return None
    strikes_all = sorted({float(r["strike"]) for r in rows})
    gaps = sorted(round(b - a, 2) for a, b in zip(strikes_all, strikes_all[1:]) if b > a)
    step = gaps[len(gaps) // 2] if gaps else 50.0
    # ATM = strike with the largest total OI (proxy) unless a hint is given
    if atm_hint is None:
        tot = {}
        for r in rows:
            tot[float(r["strike"])] = tot.get(float(r["strike"]), 0) + (r["oi"] or 0)
        atm = max(tot, key=tot.get) if tot else strikes_all[len(strikes_all) // 2]
    else:
        atm = min(strikes_all, key=lambda k: abs(k - atm_hint))
    want = {round(atm + i * step, 2) for i in range(-n_each_side, n_each_side + 1)}

    # build a minute grid from the first to last timestamp
    from datetime import datetime, timedelta
    def _p(ts):
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    t0, t1 = _p(rows[0]["received_ts"]), _p(rows[-1]["received_ts"])
    grid = []
    t = t0.replace(second=0, microsecond=0)
    while t <= t1:
        grid.append(t)
        t += timedelta(seconds=grid_sec)
    gi = {g: k for k, g in enumerate(grid)}

    series = {k: {"ce": {"oi": [None] * len(grid), "ltp": [None] * len(grid)},
                  "pe": {"oi": [None] * len(grid), "ltp": [None] * len(grid)}}
              for k in want}
    for r in rows:
        k = float(r["strike"])
        if k not in want:
            continue
        side = "ce" if str(r["option_type"]).upper() == "CE" else "pe"
        m = _p(r["received_ts"]).replace(second=0, microsecond=0)
        idx = gi.get(m)
        if idx is None:
            # snap to nearest grid minute
            idx = min(range(len(grid)), key=lambda j: abs((grid[j] - _p(r["received_ts"])).total_seconds()))
        if r["oi"] is not None:
            series[k][side]["oi"][idx] = float(r["oi"])
        if r["ltp"] is not None:
            series[k][side]["ltp"][idx] = float(r["ltp"])
    # LOCF
    for k in series:
        for side in ("ce", "pe"):
            for fld in ("oi", "ltp"):
                arr = series[k][side][fld]
                last = None
                for j in range(len(arr)):
                    if arr[j] is None:
                        arr[j] = last
                    else:
                        last = arr[j]
    return {
        "symbol": symbol.upper(), "session_date": session_date, "expiry": expiry,
        "atm": atm, "step": step,
        "grid_minutes": [g.strftime("%H:%M") for g in grid],
        "strikes": series,
        "oi_source": "ACTUAL:HISTCAP(AngelOne opnInterest)",
        "doi_source": "DERIVED (differenced)",
    }
