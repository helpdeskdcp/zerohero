"""Read-only HTTP surface for SMART_INDEX_SCALPER (spec section 29)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from .scanner import SmartIndexScalper

router = APIRouter(prefix="/api/smart-scalper", tags=["smart-index-scalper"])
_scalper = SmartIndexScalper()


@router.get("/ranking")
def api_ranking(symbols: Optional[str] = Query(None,
               description="comma list; default = SMART_SCALPER_UNIVERSE"),
               fresh: bool = False):
    """Rank the index universe by INDEX_SELECTION_SCORE. Returns #1/#2/#3 and
    why #1 won, plus the not-eligible list with the failed filters."""
    return _scalper.scan(symbols, use_cache=not fresh)


@router.get("/signal")
def api_signal(symbol: str = "NIFTY", fresh: bool = False):
    """Full candidate signal for one index (engine output + eligibility +
    selection score). No paper position is opened."""
    return _scalper.signal_for(symbol, use_cache=not fresh)
