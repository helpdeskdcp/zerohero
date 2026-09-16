"""
Anti-whipsaw: a single candle / single verification call is never enough to
promote a setup to ENTRY_READY. Pure functions operating on the recent
per-symbol history the state machine already keeps -- no state of their
own, so they're trivially testable.
"""
from __future__ import annotations

from .base_strategy import IndicatorSnapshot, MarketFeatures


def is_abnormal_spike(ind: IndicatorSnapshot, cfg) -> bool:
    """Last bar's own range vastly exceeds its ATR -- a one-off spike, not
    a trend; entries right after one are exactly the whipsaw this exists to
    avoid."""
    if ind.atr in (None, 0) or ind.n < 1:
        return False
    last_range = ind.highs[-1] - ind.lows[-1]
    return last_range > cfg.spike_atr_mult * ind.atr


def is_near_major_level(features: MarketFeatures, ind: IndicatorSnapshot, *, buffer_atr_mult: float = 0.3) -> bool:
    """True when price sits within a small ATR-buffer of a known S/R level
    -- entries there need a confirmed break/rejection, not just a score."""
    if ind.atr is None or ind.close is None:
        return False
    buf = buffer_atr_mult * ind.atr
    for level in (features.support, features.resistance):
        if level is not None and abs(ind.close - level) <= buf:
            return True
    return False


def is_sideways(ind: IndicatorSnapshot, cfg) -> bool:
    return ind.adx is not None and ind.adx < cfg.sideways_adx_max


def is_near_tie(ce_score: float, pe_score: float, cfg) -> bool:
    return abs(ce_score - pe_score) < cfg.near_tie_margin


def persistence_ok(direction_history: list[str], direction: str, cfg) -> bool:
    """`direction_history`: the most recent directions (oldest..newest,
    NOT including the current call). Persistence requires the last
    `confirmation_min_count - 1` PRIOR calls plus this one to all agree --
    i.e. `confirmation_min_count` consecutive same-direction reads total."""
    need = max(1, cfg.confirmation_min_count - 1)
    if need == 0:
        return True
    recent = direction_history[-need:]
    return len(recent) == need and all(d == direction for d in recent)


def check_confirmation(*, direction: str, ce_score: float, pe_score: float,
                       ind: IndicatorSnapshot, features: MarketFeatures,
                       direction_history: list[str], cfg) -> dict:
    """Returns {"confirmed": bool, "blockers": [...]} -- every reason a
    setup is being held at WATCH/SETUP rather than promoted."""
    blockers = []
    if direction == "NO_TRADE":
        return {"confirmed": False, "blockers": ["no_direction"]}

    if is_abnormal_spike(ind, cfg):
        blockers.append("abnormal_spike")
    if is_near_tie(ce_score, pe_score, cfg):
        blockers.append("near_tie_scores")
    if is_sideways(ind, cfg):
        blockers.append("sideways_market")
    if is_near_major_level(features, ind) and not persistence_ok(direction_history, direction, cfg):
        blockers.append("near_sr_without_confirmed_break")
    if not persistence_ok(direction_history, direction, cfg):
        blockers.append("insufficient_persistence")

    return {"confirmed": len(blockers) == 0, "blockers": blockers}
