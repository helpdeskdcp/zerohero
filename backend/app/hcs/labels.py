"""
HCS adaptive-model labels -- 3-class outcome + quality target from resolved
AUTOSCALP paper outcomes.  READ-ONLY, deterministic, pure Python.

The scalper is always LONG premium (BUY_CE or BUY_PE), so the three classes
describe whether the *trade thesis* played out -- NOT index direction:

  UP       premium moved favourably by >= THETA_R * entry-risk, or the trade hit T1
  DOWN     premium moved adversely  by >= THETA_R * entry-risk, or the trade stopped out
  NO_MOVE  neither -- timed out near flat / trailed a small gain / FLAT (no fill)

Quality target = realised R-multiple, winsorised to [_R_LO, _R_HI].

Rows come from `scalp_signals` (source=LIVE) -- the same table the existing
`app/backtest/calibration.py` and `app/hcs/adaptive.py` train on, so the new
model is directly comparable. Nothing here trains, writes, or gates.
"""
from __future__ import annotations

THETA_R = 0.5              # favourable / adverse move threshold, in units of entry risk
_R_LO, _R_HI = -2.0, 3.0   # winsor bounds for the quality (E[R]) target

CLASSES = ("UP", "DOWN", "NO_MOVE")
CLASS_IDX = {c: i for i, c in enumerate(CLASSES)}


def _risk(row: dict):
    """Entry risk in premium points = entry - stop_loss (must be > 0)."""
    try:
        r = float(row.get("entry")) - float(row.get("stop_loss"))
    except (TypeError, ValueError):
        return None
    return r if r and r > 1e-9 else None


def r_multiple(row: dict):
    """Realised R. Prefer the stored value; else points / entry-risk; else None."""
    rm = row.get("r_multiple")
    try:
        if rm is not None:
            v = float(rm)
            return v if v == v else None
    except (TypeError, ValueError):
        pass
    risk = _risk(row)
    if risk is None:
        return None
    try:
        pts = float(row.get("points"))
    except (TypeError, ValueError):
        return None
    return pts / risk


def quality_target(row: dict):
    rm = r_multiple(row)
    if rm is None:
        return None
    return max(_R_LO, min(_R_HI, rm))


def three_class(row: dict) -> str | None:
    """UP / DOWN / NO_MOVE for one resolved scalp_signals row. None if unresolved."""
    oc = str(row.get("outcome") or "").upper()
    if oc not in ("WIN", "LOSS", "FLAT"):
        return None
    if oc == "FLAT":
        return "NO_MOVE"                      # no fill / data gap -> canonical no-move
    er = str(row.get("exit_reason") or "").upper()
    if er == "TARGET":
        return "UP"
    if er == "STOP":
        return "DOWN"
    rm = r_multiple(row)
    if rm is None:
        return "UP" if oc == "WIN" else "DOWN"   # decided but no geometry -> raw sign
    if rm >= THETA_R:
        return "UP"
    if rm <= -THETA_R:
        return "DOWN"
    return "NO_MOVE"


def y_win(row: dict):
    """Binary WIN/LOSS label for the continuity head (FLAT -> None, excluded)."""
    oc = str(row.get("outcome") or "").upper()
    return 1 if oc == "WIN" else 0 if oc == "LOSS" else None


def label_row(row: dict) -> dict:
    return {"y3": three_class(row), "yq": quality_target(row), "y_win": y_win(row)}
