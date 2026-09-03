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


def _context(symbol: str) -> dict:
    """Best-effort live context from the existing pipeline. Anything missing is
    left None — the engine reports DATA_INSUFFICIENT with the exact fields."""
    ctx = {"instrument": symbol.upper(), "prev_day": {}, "bars": [], "chain": []}
    try:
        from ..connectors.angelone import _market_sdk
        from .. import market_data
        sdk = _market_sdk(require_auth=False)
        if not sdk:
            return ctx
        mkt = {"SENSEX": "BSE", "BANKEX": "BSE", "NATURALGAS": "MCX", "CRUDEOIL": "MCX"}.get(symbol.upper(), "NSE")
        snap = market_data.selection_snapshot(sdk, mkt, symbol.upper(), expiry="AUTO",
                                              option_type="BOTH", window=6,
                                              instrument="OPTION" if mkt in ("MCX", "BSE") else None)
        ctx["spot"] = snap.get("spot") or snap.get("atm")
        ctx["chain"] = [
            {"strike": r.get("strike"),
             "ce_ltp": r.get("ce_ltp"), "ce_oi": r.get("ce_oi"), "ce_oi_change": r.get("ce_oi_change"),
             "pe_ltp": r.get("pe_ltp"), "pe_oi": r.get("pe_oi"), "pe_oi_change": r.get("pe_oi_change")}
            for r in (snap.get("chain") or [])
        ]
        # prev-day OHLC via daily candles
        und = snap.get("underlying_contract") or {}
        tok, exch = und.get("token"), und.get("exchange")
        if tok:
            from datetime import datetime, timedelta, timezone
            ist = timezone(timedelta(hours=5, minutes=30))
            now = datetime.now(ist)
            d = sdk.get_candles(exch, tok, "ONE_DAY",
                                (now - timedelta(days=8)).strftime("%Y-%m-%d %H:%M"),
                                now.strftime("%Y-%m-%d %H:%M"))
            cs = d.get("candles") or []
            if len(cs) >= 2:
                p = cs[-2]
                ctx["prev_day"] = {"high": p["high"], "low": p["low"], "close": p["close"]}
            if cs:
                t = cs[-1]
                ctx["today_open"] = t["open"]
            i5 = sdk.get_candles(exch, tok, "FIVE_MINUTE",
                                 now.strftime("%Y-%m-%d 09:00"), now.strftime("%Y-%m-%d %H:%M"))
            ctx["bars"] = [{"high": c["high"], "low": c["low"], "close": c["close"], "volume": c.get("volume")}
                           for c in (i5.get("candles") or [])]
            if ctx["bars"]:
                ctx["day_high"] = max(b["high"] for b in ctx["bars"])
                ctx["day_low"] = min(b["low"] for b in ctx["bars"])
    except Exception as e:                                      # pragma: no cover
        ctx["context_error"] = f"{type(e).__name__}: {e}"
    return ctx


@router.get("/levels")
def api_levels(symbol: str = "NIFTY"):
    c = _context(symbol)
    pd = c.get("prev_day") or {}
    return {"instrument": symbol.upper(),
            "pivots": classical_pivots(pd.get("high"), pd.get("low"), pd.get("close")),
            "gann": gann_levels(pd.get("high"), pd.get("low")),
            "prev_day": pd, "today_open": c.get("today_open"),
            "day_high": c.get("day_low"), "day_low": c.get("day_low")}


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
        c = _context(sym)
        pd = c.get("prev_day") or {}
        out = _engine.evaluate(
            instrument=sym, timestamp="", prev_day=pd,
            today_open=c.get("today_open"), current_price=c.get("spot"),
            day_high=c.get("day_high"), day_low=c.get("day_low"),
            bars=c.get("bars"), chain=c.get("chain"))
        rows.append({
            "instrument": sym, "spot": c.get("spot"),
            "status": out.get("status"),
            "pivot": (out.get("mathematical_levels") or {}).get("pivots", {}).get("pivot") if out.get("status") == "OK" else None,
            "gann_balance": (out.get("mathematical_levels") or {}).get("gann", {}).get("gann_balance") if out.get("status") == "OK" else None,
            "nearest_support": (out.get("nearest_support") or {}).get("center"),
            "nearest_resistance": (out.get("nearest_resistance") or {}).get("center"),
            "market_regime": out.get("market_regime"),
            "direction": out.get("direction"),
            "confluence_score": out.get("confluence_score"),
            "confidence": out.get("confidence"),
            "signal": out.get("signal_type"),
            "missing": out.get("missing"),
        })
    return {"market_map": rows,
            "note": "confluence_score is UNCALIBRATED (default weights, no backtest)"}


@router.get("/signal")
def api_signal(symbol: str = "NIFTY", weights: Optional[str] = None):
    c = _context(symbol)
    pd = c.get("prev_day") or {}
    return _engine.evaluate(
        instrument=symbol.upper(), timestamp="", prev_day=pd,
        today_open=c.get("today_open"), current_price=c.get("spot"),
        day_high=c.get("day_high"), day_low=c.get("day_low"),
        bars=c.get("bars"), chain=c.get("chain"))
