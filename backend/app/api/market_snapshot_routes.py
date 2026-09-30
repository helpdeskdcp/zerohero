"""Read-only HTTP surface for app.market_snapshot (MCP 3.3 section 48's
GET /api/market/snapshot + GET /api/market/vix + GET /api/market/data-
quality via a DATA_CONFLICT spot-check). No order path, no gating of
anything else -- this only exposes composition/comparison functions that
already existed as plain functions."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import market_snapshot as _snap
from ..optionchain.resolve import check_data_conflict as _check_conflict

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/snapshot")
def api_market_snapshot(underlying: str, allow_network: bool = False):
    """allow_network=false (default) restricts every component to
    already-captured data -- never a live broker call, matching every
    other offline/deterministic read path in this codebase."""
    try:
        snap = _snap.build_snapshot(underlying, allow_network=allow_network)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
    return snap.to_dict()


@router.get("/vix")
def api_market_vix():
    q = _snap._latest_index_quote("INDIAVIX")
    if q is None:
        return {"status": "UNAVAILABLE", "note": "no India VIX quote captured recently"}
    return {"status": "OK", **q}


@router.get("/data-conflict")
def api_market_data_conflict(underlying: str, tolerance_pct: float = 0.5):
    """MCP 3.3 section 2/3: angelone_captured vs dhan_chain spot agreement
    for `underlying`. On-demand, read-only -- not called from any hot
    decision path (see check_data_conflict()'s own docstring for why)."""
    return _check_conflict(underlying, tolerance_pct=tolerance_pct)
