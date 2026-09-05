"""
Instrument registry + broker-master lookups (read-only) + the one write
endpoint that adds a user-defined instrument alias. Split out of app/main.py.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .. import instruments
from .. import market_data
from .schemas import InstrumentRequest

router = APIRouter()


@router.get("/api/instruments")
def api_instruments():
    """What the connector can resolve by name (seeds + user additions)."""
    reg = instruments.registry()
    return {
        "instruments": [
            {"name": k, "exchange": v.get("exchange"), "symboltoken": v.get("symboltoken"),
             "market": v.get("market"), "aliases": v.get("aliases") or []}
            for k, v in sorted(reg.items())
        ],
        "timeframes": ["1m", "3m", "5m", "15m", "1h"],
    }


@router.get("/api/market-instruments")
def api_market_instruments(market: str = Query("NSE")):
    """Current valid symbols from the official AngelOne master (read-only)."""
    market = str(market or "NSE").upper()
    if market not in ("NSE", "MCX"):
        return {"market": market, "instruments": [], "data_status": "DATA_UNAVAILABLE"}
    try:
        from ..connectors.angelone import _market_sdk
        sdk = _market_sdk(require_auth=False)
        if not sdk:
            return {"market": market, "instruments": [], "data_status": "DATA_UNAVAILABLE"}
        return {"market": market, "instruments": market_data.available_symbols(sdk, market),
                "data_status": "OK", "source": "ANGELONE_SDK"}
    except Exception:
        return {"market": market, "instruments": [], "data_status": "DATA_UNAVAILABLE"}


@router.get("/api/market-selection")
def api_market_selection(market: str = Query("NSE"), symbol: str = Query(...),
                         expiry: str = Query("AUTO"), option_type: str = Query("BOTH"),
                         instrument: Optional[str] = Query(None),
                         window: int = Query(5, ge=0, le=20)):
    """Read-only resolved contract and display snapshot for the Run form."""
    try:
        from ..connectors.angelone import _market_sdk
        sdk = _market_sdk(require_auth=False)
        if not sdk:
            return {"status": "DATA_UNAVAILABLE", "data_status": "DATA_UNAVAILABLE",
                    "market": market, "symbol": symbol, "reason": "SDK unavailable"}
        return market_data.selection_snapshot(sdk, market, symbol, expiry=expiry,
                                              option_type=option_type, window=window, instrument=instrument)
    except Exception:
        return {"status": "DATA_UNAVAILABLE", "data_status": "DATA_UNAVAILABLE",
                "market": market, "symbol": symbol, "reason": "market data unavailable"}


@router.post("/api/instruments")
def api_add_instrument(req: InstrumentRequest):
    try:
        added = instruments.add_instrument(
            req.name, req.exchange, req.symboltoken, req.market, req.aliases)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"added": added, "registry": instruments.registry()}
