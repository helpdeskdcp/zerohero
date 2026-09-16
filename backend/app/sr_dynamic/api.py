"""
Live-wiring spec section 18 (observability): /api/sr/status exposes the
LiveSRState the decision pipeline last computed for each symbol (populated
by app.engines.scalp_strategy.decide_from_context's unconditional
refresh_and_store call -- see live_state.py). Read-only, no recompute here:
this endpoint must never block the web server or trigger a new SR
calculation.
"""
from __future__ import annotations

from fastapi import APIRouter

from .live_state import get_all_latest, get_latest

router = APIRouter()


@router.get("/api/sr/status")
def api_sr_status_all():
    return {"symbols": get_all_latest()}


@router.get("/api/sr/status/{symbol}")
def api_sr_status_symbol(symbol: str):
    st = get_latest(symbol)
    if st is None:
        return {"symbol": symbol.upper(), "status": "no_data",
                "reason": "no live decision cycle has computed SR state for this symbol yet"}
    return st
