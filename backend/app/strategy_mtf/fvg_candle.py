"""
FVG (imbalance) + inside-candle -- CONFIRMATION-ONLY inputs, per the
approved architecture (#12): "They should NOT independently generate a
trade against the higher-timeframe bias." This module never returns a
CE/PE decision by itself -- only a confirmation flag + direction (which the
caller may use to nudge a score, never to override HTF structure).

FVG reuses `app.liquidity_sweep.confirmation.fvg` directly (already tested
this session) rather than reimplementing the 3-candle imbalance check.
Inside-candle detection doesn't exist anywhere in this codebase yet, so
it's implemented fresh here (a plain, standard definition: the current
bar's full range sits inside the previous bar's range).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ..liquidity_sweep.confirmation import fvg as _fvg


@dataclass
class ImbalanceConfirmation:
    fvg_confirmed: bool
    fvg_direction: str | None
    inside_candle: bool
    inside_candle_direction: str | None   # the direction a break of the inside bar's range would confirm
    boost: float                          # 0.0-1.0, a confirmation-only ADDITIVE nudge, never a trigger

    def to_dict(self) -> dict:
        return asdict(self)


def _is_inside_candle(bars: list[dict]) -> bool:
    if len(bars) < 2:
        return False
    mother, inside = bars[-2], bars[-1]
    return inside["h"] <= mother["h"] and inside["l"] >= mother["l"]


def evaluate(bars: list[dict], *, candidate_direction: str | None = None) -> ImbalanceConfirmation:
    """`candidate_direction`: the direction the CASCADE has already
    proposed (BULLISH/BEARISH) -- this module only reports whether FVG/
    inside-candle AGREE with it (a boost), never proposes its own
    direction against that context."""
    fvg_res = _fvg(bars)
    fvg_confirmed = bool(fvg_res.get("confirmed"))
    fvg_direction = fvg_res.get("direction") if fvg_confirmed else None

    inside = _is_inside_candle(bars)
    # an inside bar itself has no direction until it breaks one way; report
    # the direction that would agree with the candidate, for the caller to
    # combine -- never invented independently of the candidate.
    inside_direction = candidate_direction if inside else None

    boost = 0.0
    if candidate_direction is not None:
        if fvg_confirmed and fvg_direction == candidate_direction:
            boost += 0.6
        if inside:
            boost += 0.4
    boost = min(1.0, boost)

    return ImbalanceConfirmation(
        fvg_confirmed=fvg_confirmed, fvg_direction=fvg_direction,
        inside_candle=inside, inside_candle_direction=inside_direction, boost=round(boost, 2),
    )
