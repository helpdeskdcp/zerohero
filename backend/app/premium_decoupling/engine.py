"""Reads already-captured spot + ATM CE/PE ticks from market_history.db and
computes one window's spot-vs-premium classification. Read-only against
market_history.db -- never writes there, never calls the broker."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..optionchain.chain import expiry_phase, strike_step_for
from .classify import classify

_ROOT = Path(__file__).resolve().parents[2]
_MARKET_DB = _ROOT / "data" / "market_history.db"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _first_last(conn, sql: str, params: tuple) -> tuple[float | None, float | None, str | None, str | None]:
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return None, None, None, None
    return rows[0][1], rows[-1][1], rows[0][0], rows[-1][0]


def _pct(open_v: float | None, close_v: float | None) -> float | None:
    if open_v is None or close_v is None or open_v == 0:
        return None
    return (close_v - open_v) / abs(open_v) * 100.0


def _nearest_strike(conn, underlying: str, expiry: str, option_type: str,
                     start_ts: str, end_ts: str, target: float) -> float | None:
    rows = conn.execute(
        "SELECT DISTINCT strike FROM quote_snapshots WHERE symbol=? AND kind='OPTION' "
        "AND expiry=? AND option_type=? AND received_ts BETWEEN ? AND ?",
        (underlying, expiry, option_type, start_ts, end_ts)).fetchall()
    if not rows:
        return None
    return min((r[0] for r in rows), key=lambda s: abs(s - target))


def _pick_expiry(conn, underlying: str, start_ts: str, end_ts: str) -> str | None:
    rows = conn.execute(
        "SELECT DISTINCT expiry FROM quote_snapshots WHERE symbol=? AND kind='OPTION' "
        "AND received_ts BETWEEN ? AND ?", (underlying, start_ts, end_ts)).fetchall()
    if not rows:
        return None
    def _dte(e):
        d = expiry_phase(e).get("dte")
        return d if (d is not None and d >= 0) else 10 ** 6
    return min((r[0] for r in rows), key=_dte)


def compute_window(underlying: str, window_sec: int = 300, end_ts: str | None = None,
                    db_path: Path | str | None = None) -> dict:
    """Classify the spot-vs-ATM-premium relationship over the `window_sec`
    seconds ending at `end_ts` (default: now). Returns INSUFFICIENT_DATA if
    the captured history doesn't cover the window (e.g. off-hours)."""
    underlying = str(underlying or "").upper()
    end_dt = datetime.now(timezone.utc) if end_ts is None else datetime.fromisoformat(
        end_ts.replace("Z", "+00:00"))
    start_dt = end_dt - timedelta(seconds=window_sec)
    start_iso, end_iso = _iso(start_dt), _iso(end_dt)

    out = {"underlying": underlying, "window_sec": window_sec,
           "window_start_ts": start_iso, "window_end_ts": end_iso,
           "classification": "INSUFFICIENT_DATA", "explanation": "no data", }

    db_path = Path(db_path) if db_path else _MARKET_DB
    if not db_path.exists():
        out["explanation"] = f"no such db: {db_path}"
        return out

    conn = sqlite3.connect(str(db_path))
    try:
        s_open, s_close, s_open_ts, s_close_ts = _first_last(
            conn, "SELECT received_ts, ltp FROM quote_snapshots WHERE symbol=? AND kind='INDEX' "
                  "AND received_ts BETWEEN ? AND ? ORDER BY received_ts ASC",
            (underlying, start_iso, end_iso))
        if s_open is None or s_close is None:
            out["explanation"] = "no INDEX spot ticks captured in this window"
            return out
        spot_delta = s_close - s_open
        spot_delta_pct = _pct(s_open, s_close)
        out.update(spot_open=s_open, spot_close=s_close, spot_delta=spot_delta,
                    spot_delta_pct=spot_delta_pct)

        expiry = _pick_expiry(conn, underlying, start_iso, end_iso)
        if expiry is None:
            out["explanation"] = "no OPTION ticks captured in this window"
            return out
        out["expiry"] = expiry

        target = round(s_close / strike_step_for(underlying)) * strike_step_for(underlying)
        ce_delta_pct = pe_delta_pct = None
        for opt_type, key in (("CE", "ce"), ("PE", "pe")):
            strike = _nearest_strike(conn, underlying, expiry, opt_type, start_iso, end_iso, target)
            if strike is None:
                out[f"{key}_open"] = out[f"{key}_close"] = out[f"{key}_delta_pct"] = None
                continue
            o, c, _, _ = _first_last(
                conn, "SELECT received_ts, ltp FROM quote_snapshots WHERE symbol=? AND kind='OPTION' "
                      "AND expiry=? AND option_type=? AND strike=? AND received_ts BETWEEN ? AND ? "
                      "ORDER BY received_ts ASC",
                (underlying, expiry, opt_type, strike, start_iso, end_iso))
            pct = _pct(o, c)
            out[f"{key}_strike"] = strike
            out[f"{key}_open"] = o
            out[f"{key}_close"] = c
            out[f"{key}_delta"] = (None if (o is None or c is None) else c - o)
            out[f"{key}_delta_pct"] = pct
            if key == "ce":
                ce_delta_pct = pct
            else:
                pe_delta_pct = pct

        result = classify(spot_delta_pct, ce_delta_pct, pe_delta_pct)
        out.update(result)
        return out
    finally:
        conn.close()
