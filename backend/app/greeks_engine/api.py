"""Read-only HTTP surface for the Greeks Engine (derived exposure metrics)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from .engine import greeks_engine

router = APIRouter(prefix="/api/greeks-engine", tags=["greeks-engine"])


@router.get("/status")
def status():
    return greeks_engine().status()


@router.get("/latest")
def latest(underlying: str = "NIFTY", expiry: Optional[str] = None):
    return greeks_engine().latest(underlying, expiry) or {"status": "NO_DATA"}


@router.get("/exposure")
def exposure(underlying: str = "NIFTY", expiry: Optional[str] = None,
             as_of: Optional[str] = None, since: Optional[str] = None,
             limit: int = Query(2000, le=20000)):
    """Look-ahead-safe history: as_of_ts <= as_of, oldest first."""
    return greeks_engine().history(underlying, expiry=expiry, as_of=as_of,
                                   since=since, limit=limit)


@router.get("/runs")
def runs(limit: int = Query(50, le=500)):
    return greeks_engine().runs(limit)
