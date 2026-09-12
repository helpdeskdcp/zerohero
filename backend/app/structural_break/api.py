"""
Read-only HTTP surface for the Structural Break / Adaptive Model layer.

Same convention as greeks_engine/api.py and optionchain/api.py: a GET here
triggers computation over already-captured data (evaluator.evaluate_now()
re-reads scalp_signals and re-scores it), never a write to order execution,
broker credentials, or any live trading-risk state. No route in this file
accepts a POST or mutates anything outside this layer's own in-memory
state-machine registry and its own dedicated audit-log DB file.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from . import evaluator
from .audit_log import audit_log

router = APIRouter(prefix="/api/structural-break", tags=["structural-break"])


@router.get("/status")
def status():
    return {"tracked": evaluator.tracked_scopes()}


@router.get("/status/{symbol}")
def status_symbol(symbol: str, regime: Optional[str] = None, source: str = "LIVE"):
    return evaluator.evaluate_now(symbol, regime=regime, source=source)


@router.get("/history")
def history(symbol: Optional[str] = None, state_to: Optional[str] = None,
            limit: int = Query(200, le=2000)):
    return {"events": audit_log().history(symbol, state_to=state_to, limit=limit)}


@router.get("/events/{symbol}")
def why(symbol: str, limit: int = Query(5, le=100)):
    """Answers spec section I's own question directly: why did the model
    decide the previous model was no longer valid, for this symbol."""
    return {"symbol": symbol, "structural_break_events": audit_log().why(symbol, limit=limit)}
