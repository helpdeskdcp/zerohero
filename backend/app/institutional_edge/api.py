"""
Read-only HTTP surface for the Institutional Edge layer.

Same convention as structural_break/api.py and greeks_engine/api.py: a GET
here triggers computation over already-captured data, never a write to
order execution, broker credentials, or any live trading-risk state.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from . import conditions, costs
from .evaluator import evaluate_now, tracked_edges
from .store import store

router = APIRouter(prefix="/api/institutional-edge", tags=["institutional-edge"])


@router.get("/status")
def status():
    return {"tracked": tracked_edges()}


@router.get("/status/{instrument}")
def status_instrument(instrument: str, condition: str = Query(...), source: str = "LIVE"):
    return evaluate_now(instrument, condition, source=source)


@router.get("/conditions")
def conditions_list():
    return {"conditions": conditions.condition_labels()}


@router.get("/costs")
def costs_known():
    """Transparency on exactly which instruments have a validated realistic
    cost model -- see costs.py's own module docstring for why the rest are
    deliberately left uncalibrated rather than guessed."""
    return {"known_profiles": costs.known_profiles()}


@router.get("/history")
def history(instrument: Optional[str] = None, condition: Optional[str] = None,
            state: Optional[str] = None, limit: int = Query(200, le=2000)):
    return {"evaluations": store().history(instrument, condition, state=state, limit=limit)}
