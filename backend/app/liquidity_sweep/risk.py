"""
SL / target / position sizing -- sections 7, 8, 10, 14, 15.

Position sizing wraps the EXISTING app.engines.risk_engine.run_risk_engine
directly rather than reimplementing it -- that function already computes
exactly this brief's formula (risk_budget = capital*risk_pct/100; qty =
floor(risk_budget/per_unit_risk); lots = floor(qty/lot_size); margin-capped)
plus daily-loss/consecutive-loss/max-trades gates. The R:R check inside
run_risk_engine operates on the OPTION premium's own entry/stop/target,
which is a different geometry from this brief's UNDERLYING-index 2R rule
(option premium doesn't move linearly with the index) -- so that specific
gate is neutralised here (`rr_min=0`) to avoid double-gating on a
non-equivalent number; the real 2R enforcement is `build_plan()` below,
on the underlying.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ..engines.risk_engine import run_risk_engine

MIN_RR = 2.0   # sections 8/14: minimum planned R:R is 1:2, a floor not a ceiling


@dataclass
class TradePlan:
    status: str                      # "OK" | "NO_TRADE"
    direction: str | None = None
    entry: float | None = None
    stop_loss: float | None = None
    target_1: float | None = None
    risk_distance: float | None = None
    reward_distance: float | None = None
    rr: float | None = None
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def build_plan(*, direction: str, entry: float, sweep_extreme: float, buffer_pts: float,
                next_liquidity_target: float | None = None) -> TradePlan:
    """SL = sweep extreme +/- buffer (never widened after entry -- this
    function is only ever called once, at signal time). Target = the next
    real liquidity pool IF it's farther than 2R (a better target than the
    bare minimum), otherwise exactly 2R -- section 8's "next HTF liquidity
    pool OR minimum 2R" read literally: 2R is the floor a target must clear,
    never a reason to settle for less than a genuinely closer real pool
    would give, and never satisfied by manufacturing a target that isn't 2R
    when no real pool qualifies."""
    if direction == "BULLISH":
        sl = sweep_extreme - buffer_pts
        risk = entry - sl
    elif direction == "BEARISH":
        sl = sweep_extreme + buffer_pts
        risk = sl - entry
    else:
        return TradePlan(status="NO_TRADE", direction=direction, reason=f"unknown direction {direction!r}")

    if risk <= 0:
        return TradePlan(status="NO_TRADE", direction=direction,
                         reason="non-positive stop distance -- SL would need to be on the wrong side of entry")

    min_target = entry + MIN_RR * risk if direction == "BULLISH" else entry - MIN_RR * risk
    target = min_target
    if next_liquidity_target is not None:
        better = (next_liquidity_target > min_target) if direction == "BULLISH" else (next_liquidity_target < min_target)
        if better:
            target = next_liquidity_target

    reward = abs(target - entry)
    rr = round(reward / risk, 3)
    if rr < MIN_RR - 1e-9:
        return TradePlan(status="NO_TRADE", direction=direction, entry=entry, stop_loss=round(sl, 4),
                         risk_distance=round(risk, 4), rr=rr,
                         reason=f"R:R {rr} < minimum {MIN_RR}")

    return TradePlan(status="OK", direction=direction, entry=entry, stop_loss=round(sl, 4),
                     target_1=round(target, 4), risk_distance=round(risk, 4),
                     reward_distance=round(reward, 4), rr=rr)


def size_position(*, option_entry: float, option_stop: float, capital: float,
                  risk_pct: float, lot_size: int, daily_pnl: float = 0.0,
                  consecutive_losses: int = 0, trades_today: int = 0) -> dict:
    """Reuses run_risk_engine for the position-sizing/margin/daily-loss math
    only -- see module docstring for why its own R:R gate is neutralised."""
    per_unit_risk = abs(option_entry - option_stop)
    option_target = option_entry + MIN_RR * per_unit_risk   # BUY-only (options are always long premium here)
    return run_risk_engine({
        "signal": {"entry_ref": option_entry, "stop_loss": option_stop,
                  "target_1": option_target, "direction": "BUY"},
        "account": {"capital": capital, "risk_pct": risk_pct},
        "instrument": {"lot_size": lot_size},
        "state": {"daily_pnl": daily_pnl, "consecutive_losses": consecutive_losses,
                 "trades_today": trades_today},
        "limits": {"rr_min": 0.0},
    })
