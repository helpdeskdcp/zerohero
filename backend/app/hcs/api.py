"""Read-only HCS endpoints. No order path, no live-signal emission."""
from __future__ import annotations

from fastapi import APIRouter

from . import calibrate as _cal
from . import engine as _eng
from . import forward_test as _ft

router = APIRouter(prefix="/api/hcs", tags=["hcs"])


@router.get("/evaluate")
def hcs_evaluate(symbol: str | None = None, session_date: str | None = None):
    """Shadow HCS evaluation of the latest live decision per symbol."""
    return _eng.evaluate(symbol=symbol, session_date=session_date)


@router.get("/calibration-report")
def hcs_calibration_report():
    """Calibration of the score->probability curve on resolved AUTOSCALP outcomes."""
    return _cal.report()


@router.get("/forward-test")
def hcs_forward_test():
    """Replay the HCS A+ gate over every resolved autoscalp signal + the live log."""
    return {"replay": _ft.replay(), "live_log": _ft.log_summary()}


@router.get("/adaptive")
def hcs_adaptive():
    """The adaptive online-logit model: state, top weights, and a walk-forward
    Brier/ECE comparison vs the existing closed-form logistic. Advisory / SHADOW."""
    from . import adaptive as _adp
    return _adp.refit_and_report()


@router.get("/adaptive-mc")
def hcs_adaptive_mc(persist: bool = False):
    """Tier-A multinomial-logit SHADOW model: 3-class (UP / DOWN / NO_MOVE) +
    isotonic-calibrated P(win) + expected-R, with an expanding-window
    walk-forward vs the closed-form logistic baseline. Advisory only -- not
    wired into any gate. `persist=true` writes data/hcs_adaptive_mc_model.json."""
    from . import adaptive_mc as _amc
    return _amc.refit_and_report(persist=persist)
