"""Read-only status of the Hybrid OrderFlow shadow runner. No order path."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/hybrid_orderflow", tags=["hybrid-orderflow"])
_runner = None


def set_runner(r) -> None:
    global _runner
    _runner = r


@router.get("/status")
def status():
    if _runner is None:
        raise HTTPException(status_code=503, detail="hybrid_orderflow shadow runner not running (HYBRID_ORDERFLOW_SHADOW=0 or policy invalid)")
    return _runner.status()
