"""Read-only HTTP surface for app.market_snapshot (MCP 3.3 section 48's
GET /api/market/snapshot + GET /api/market/vix). No order path, no gating
of anything else -- this only exposes the atomic-snapshot composition
that already existed as a plain function."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import market_snapshot as _snap

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
