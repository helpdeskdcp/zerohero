"""
Read-only single-symbol analysis reads: reversal detection + the deterministic
turning-point engine. Split out of app/main.py.
"""
from __future__ import annotations

from fastapi import APIRouter

from .. import db
from ..reversal import detect_reversal

router = APIRouter()


@router.get("/api/reversal")
def api_reversal(symbol: str, timeframe: str = "15m"):
    """Resistance→support / support→resistance reversal read for a symbol,
    with a concrete CE/PE + entry / stop / target if a turn is firing."""
    from ..connectors import angelone as _a
    conn = _a.fetch_candles(market=None, symbol=symbol, exchange=None, symboltoken=None,
                            interval=None, fromdate=None, todate=None, timeframe=timeframe,
                            instrument="FUT")
    if conn.get("data_status") != "OK":
        return {"symbol": symbol, "data_status": conn.get("data_status"),
                "reason": conn.get("reason"), "reversal": None}
    r = detect_reversal(conn["candles"])
    r["symbol"] = symbol
    r["timeframe"] = timeframe
    return r


@router.get("/api/turning-point")
def api_turning_point(symbol: str, timeframe: str = "5m"):
    """Deterministic turning-point read for a symbol: direction, up/down turn
    zones, next High/Low + Swing zones with probabilities, confidence, expected
    move, and a Risk-Engine-ready trade_ref. Predicts ZONES, not prices."""
    from ..connectors import angelone as _a
    from ..engines.turning_point_engine import run_turning_point_engine
    from ..engines.signal_engine import run_signal_engine
    from .. import tp_calibration
    conn = _a.fetch_candles(market=None, symbol=symbol, exchange=None, symboltoken=None,
                            interval=None, fromdate=None, todate=None, timeframe=timeframe,
                            instrument="FUT")
    if conn.get("data_status") != "OK":
        return {"symbol": symbol, "data_status": conn.get("data_status"),
                "reason": conn.get("reason"), "direction": "NO_TURN"}
    sig = run_signal_engine({"symbol": symbol, "timeframe": timeframe, "source": "ANGELONE",
                             "data_status": "OK", "candles": conn["candles"], "config": {}})
    tp = run_turning_point_engine({"candles": conn["candles"], "signal_calc": sig.get("calculations"),
                                   "calibration": tp_calibration.load()})
    tp["symbol"] = symbol
    tp["timeframe"] = timeframe
    return tp


@router.get("/api/turning-point/calibration")
def api_tp_calibration():
    """Current learned sigmoid (k, b) + feature weights + resolved-prediction
    stats. Deterministic: same tp_predictions rows -> same numbers."""
    from .. import tp_calibration
    cal = tp_calibration.load()
    with db.db() as conn:
        by_outcome = {r["outcome"] or "UNRESOLVED": r["c"] for r in conn.execute(
            "SELECT outcome, COUNT(*) AS c FROM tp_predictions GROUP BY outcome")}
        recent = [dict(r) for r in conn.execute(
            "SELECT ts,symbol,timeframe,direction,confidence,p_up,outcome,signed_outcome,err_pts "
            "FROM tp_predictions WHERE resolved=1 ORDER BY id DESC LIMIT 25")]
        total = conn.execute("SELECT COUNT(*) AS c FROM tp_predictions").fetchone()["c"]
    hit = sum(v for k, v in by_outcome.items() if k in ("DIRECTION_HIT", "ZONE_HIT", "BOTH"))
    graded = sum(v for k, v in by_outcome.items() if k not in ("UNRESOLVED", "TIMEOUT", None))
    return {"calibration": cal, "predictions_total": total,
            "by_outcome": by_outcome,
            "hit_rate": round(hit / graded, 3) if graded else None,
            "recent_resolved": recent}
