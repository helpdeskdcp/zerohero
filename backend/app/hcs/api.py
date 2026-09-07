"""Read-only HCS endpoints. No order path, no live-signal emission."""
from __future__ import annotations

from fastapi import APIRouter

from . import calibrate as _cal
from . import engine as _eng

router = APIRouter(prefix="/api/hcs", tags=["hcs"])


@router.get("/evaluate")
def hcs_evaluate(symbol: str | None = None, session_date: str | None = None):
    """Shadow HCS evaluation of the latest live decision per symbol."""
    return _eng.evaluate(symbol=symbol, session_date=session_date)


@router.get("/calibration-report")
def hcs_calibration_report():
    """Calibration of the score->probability curve on resolved AUTOSCALP outcomes."""
    return _cal.report()
