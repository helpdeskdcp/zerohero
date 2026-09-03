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


# --------------------------------------------------------------- SLICE 4: paper trade state machine
@router.get("/signals")
def api_signals(instrument: Optional[str] = None, limit: int = 100):
    from .. import db
    return {"signals": db.list_smart_scalper_signals(limit=min(limit, 500), instrument=instrument)}


@router.get("/paper/state")
def api_paper_state(signal_id: Optional[str] = None, trade_id: Optional[str] = None, limit: int = 100):
    from .. import db
    return {"transitions": db.list_smart_scalper_states(signal_id=signal_id, trade_id=trade_id,
                                                       limit=min(limit, 500))}


@router.get("/paper/positions")
def api_paper_positions():
    from .. import db
    return {"open": db.list_trades(status="OPEN", strategy="SMART_SCALPER", limit=50),
            "recent_closed": db.list_trades(status="CLOSED", strategy="SMART_SCALPER", limit=25),
            "live_trading": False}


@router.get("/paper/journal")
def api_paper_journal(limit: int = 5000):
    from .journal import journal
    return journal(limit)


@router.post("/paper/evaluate")
def api_paper_evaluate(symbols: Optional[str] = None, profile: Optional[str] = None,
                       dry_run: bool = True):
    """Scan -> pre-entry state machine. With dry_run=false (and safeguards
    passing) opens ONE paper position for the top-ranked index. Never a real order."""
    from .paper_engine import SmartScalperPaperEngine
    return SmartScalperPaperEngine(profile=profile).evaluate(symbols, dry_run=dry_run, use_cache=False)


@router.post("/paper/manage")
def api_paper_manage(profile: Optional[str] = None):
    """Mark every open SMART_SCALPER paper trade + run the in-trade state machine."""
    from .paper_engine import SmartScalperPaperEngine
    return SmartScalperPaperEngine(profile=profile).manage(use_cache=False)


# --------------------------------------------------------------- SLICE 5: historical replay / backtest
@router.get("/replay/sessions")
def api_replay_sessions(symbols: Optional[str] = None):
    """The (instrument, session) pairs in market_history.db that the strict-causal
    replay can run — needs both intraday candles and a real option chain."""
    from .replay import SmartScalperReplay
    return {"sessions": SmartScalperReplay().available_sessions(symbols),
            "data_source": "market_history.db (captured, ACTUAL)"}


@router.get("/replay")
def api_replay(symbols: Optional[str] = None, profile: Optional[str] = None,
               step_min: int = 3, max_hold_min: int = 25):
    """Strict-causal historical replay over the captured sessions -> simulated
    trades + metrics per profile / instrument / market-regime + calibration
    reliability. Below the sample gate the aggregate is `descriptive_only` and
    the calibration table is withheld (spec section 26). No order path; nothing
    is written to ai_paper_trades."""
    from .replay import SmartScalperReplay
    return SmartScalperReplay().run(symbols, step_min=max(1, step_min),
                                    profiles=[profile] if profile else None,
                                    max_hold_min=max(1, max_hold_min))
