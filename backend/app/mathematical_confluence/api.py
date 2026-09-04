"""Read-only HTTP surface for MATHEMATICAL_CONFLUENCE_ENGINE_V1 (section 29).

No side effects, no order path. Pulls prev-day OHLC + intraday bars + option
chain from the existing app data sources, runs the engine, returns the result.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from . import MathematicalConfluenceEngine
from .levels import classical_pivots, gann_levels, normalized_levels
from .confluence import cluster_levels
from .oi_confluence import oi_matrix

router = APIRouter(prefix="/api/mathematics", tags=["mathematical-confluence"])
_engine = MathematicalConfluenceEngine()


from .context import market_context as _context


@router.get("/levels")
def api_levels(symbol: str = "NIFTY"):
    c = _context(symbol)
    pd = c.get("prev_day") or {}
    return {"instrument": symbol.upper(),
            "pivots": classical_pivots(pd.get("high"), pd.get("low"), pd.get("close")),
            "gann": gann_levels(pd.get("high"), pd.get("low")),
            "prev_day": pd, "today_open": c.get("today_open"),
            "day_high": c.get("day_high"), "day_low": c.get("day_low")}


@router.get("/confluence")
def api_confluence(symbol: str = "NIFTY"):
    c = _context(symbol)
    pd = c.get("prev_day") or {}
    spot = c.get("spot")
    if not spot:
        return {"status": "DATA_INSUFFICIENT", "missing": ["spot"]}
    lv = normalized_levels(pdh=pd.get("high"), pdl=pd.get("low"), pdc=pd.get("close"),
                           today_open=c.get("today_open"),
                           day_high=c.get("day_high"), day_low=c.get("day_low"))
    return {"instrument": symbol.upper(), "spot": spot,
            "zones": cluster_levels(lv, spot)}


@router.get("/oi")
def api_oi(symbol: str = "NIFTY"):
    c = _context(symbol)
    if not c.get("chain") or not c.get("spot"):
        return {"status": "DATA_INSUFFICIENT", "missing": ["option_chain" if not c.get("chain") else "spot"]}
    return oi_matrix(c["chain"], c["spot"])


@router.get("/market-map")
def api_market_map(symbols: str = Query("NIFTY,BANKNIFTY,SENSEX")):
    rows = []
    for sym in [s.strip().upper() for s in symbols.split(",") if s.strip()]:
        try:
            c = _context(sym, allow_rest_fallback=False)   # bulk view: cache/histcap/feed only, always fast
            pd = c.get("prev_day") or {}
            out = _engine.evaluate(
                instrument=sym, timestamp="", prev_day=pd,
                today_open=c.get("today_open"), current_price=c.get("spot"),
                day_high=c.get("day_high"), day_low=c.get("day_low"),
                bars=c.get("bars"), chain=c.get("chain"))
            ml = out.get("mathematical_levels") or {}
            rows.append({
                "instrument": sym, "spot": c.get("spot"),
                "status": out.get("status"),
                "pivot": (ml.get("pivots") or {}).get("pivot"),
                "gann_balance": (ml.get("gann") or {}).get("gann_balance"),
                "nearest_support": (out.get("nearest_support") or {}).get("center"),
                "nearest_resistance": (out.get("nearest_resistance") or {}).get("center"),
                "market_regime": out.get("market_regime"),
                "direction": out.get("direction"),
                "confluence_score": out.get("confluence_score"),
                "confidence": out.get("confidence"),
                "signal": out.get("signal_type"),
                "missing": out.get("missing"),
            })
        except Exception as e:                                  # one bad symbol must not 500 the map
            rows.append({"instrument": sym, "status": "ERROR", "error": f"{type(e).__name__}: {e}"})
    return {"market_map": rows,
            "note": "confluence_score is UNCALIBRATED (default weights, no backtest)"}


@router.get("/signal")
def api_signal(symbol: str = "NIFTY", weights: Optional[str] = None):
    c = _context(symbol)
    pd = c.get("prev_day") or {}
    out = _engine.evaluate(
        instrument=symbol.upper(), timestamp="", prev_day=pd,
        today_open=c.get("today_open"), current_price=c.get("spot"),
        day_high=c.get("day_high"), day_low=c.get("day_low"),
        bars=c.get("bars"), chain=c.get("chain"))
    dq = c.get("data_quality") or {}
    out["data_source"] = c.get("source", "?")
    out["spot_source"] = dq.get("spot")
    out["chain_source"] = dq.get("option_chain")
    out["stale"] = bool(c.get("stale"))
    return out
