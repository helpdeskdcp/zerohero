"""Read-only HTTP surface for SMART_INDEX_SCALPER (spec section 29)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from .scanner import SmartIndexScalper
from .profiles import get_profile, list_profiles
from . import option_selector
from ..mathematical_confluence.context import market_context

router = APIRouter(prefix="/api/smart-scalper", tags=["smart-index-scalper"])


def _sc(profile: Optional[str] = None) -> SmartIndexScalper:
    return SmartIndexScalper(profile=profile)


@router.get("/ranking")
def api_ranking(symbols: Optional[str] = Query(None,
               description="comma list; default = SMART_SCALPER_UNIVERSE"),
               profile: Optional[str] = None, fresh: bool = False):
    """Rank the index universe by INDEX_SELECTION_SCORE. Returns #1/#2/#3 and
    why #1 won, the not-eligible list with failed filters, and — for a
    directional eligible setup — the picked CE/PE contract."""
    return _sc(profile).scan(symbols, use_cache=not fresh)


@router.get("/signal")
def api_signal(symbol: str = "NIFTY", profile: Optional[str] = None, fresh: bool = False):
    """Full candidate signal for one index (engine output + eligibility +
    selection score + selected option). No paper position is opened."""
    return _sc(profile).signal_for(symbol, use_cache=not fresh)


@router.get("/option")
def api_option(symbol: str = "NIFTY", direction: str = "CE",
               profile: Optional[str] = None, fresh: bool = False):
    """Slice 3 — the CE/PE contract selection for `symbol` given a direction,
    standalone. Reuses option_engine.analyse_leg + select_option."""
    ctx = market_context(symbol, use_cache=not fresh)
    if not ctx.get("chain") or not ctx.get("spot"):
        return {"status": "DATA_INSUFFICIENT",
                "missing": ["option_chain" if not ctx.get("chain") else "spot"]}
    prof = get_profile(profile)
    from ..autoscalp.runner import _sym_meta
    step = float(_sym_meta(symbol.upper()).get("strike_step", 50.0))
    return option_selector.select(
        direction=direction.upper(), spot=ctx["spot"], chain=ctx["chain"],
        atm=ctx.get("atm"), strike_step=step,
        allowed_option_distance=int(prof.get("allowed_option_distance", 2)))


@router.get("/profiles")
def api_profiles():
    return {"profiles": list_profiles(),
            "default": get_profile()["name"],
            "note": "UNCALIBRATED defaults (spec section 25/26). Risk controls still route "
                    "through autoscalp.safeguards on any paper entry."}
