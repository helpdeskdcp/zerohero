"""Primary (SOLD) leg selection -- the piece Phase 1/2a deliberately left
undefined (selector.py only answers "given a primary, what's the best
hedge"; nothing before this module ever decided WHICH strike to sell).

Strategy, explicit and simple (same "conservative default, not backtested-
optimal" discipline as selector.py's own DEFAULT_* constants): sell the
strike with the LARGEST open interest on its side, at least
`min_distance_pct` OTM from spot -- a real, already-existing OI concentration
acts as a wall the market is statistically less likely to reach, and premium
sold there is furthest from the money for a given amount of OI support.
Direction-agnostic: whichever side (CE or PE) has the larger qualifying wall
wins, unless `side` is forced by the caller. No regime/trend input, no ML --
that keeps this auditable and matches the rest of this module's "deterministic,
no AI" convention. NOT backtested; this is a reasonable starting rule, not a
validated edge, exactly like selector.py's own limits.
"""
from __future__ import annotations

from dataclasses import dataclass

from .selector import PrimaryLeg

DEFAULT_MIN_DISTANCE_PCT = 1.5   # OTM distance from spot, % of spot
DEFAULT_MIN_OI = 500


@dataclass
class PrimarySelection:
    status: str                    # "SELECTED" | "NO_TRADE"
    leg: PrimaryLeg | None
    side: str | None
    reason: str


def _best_wall(chain, side_attr: str, option_type: str, *, spot: float,
               min_distance_pct: float, min_oi: float):
    best = None
    for row in chain.rows or []:
        leg = getattr(row, side_attr, None)
        if leg is None or leg.ltp is None or leg.oi is None:
            continue
        if leg.oi < min_oi:
            continue
        if option_type == "CE" and row.strike <= spot:
            continue
        if option_type == "PE" and row.strike >= spot:
            continue
        dist_pct = abs(row.strike - spot) / spot * 100.0 if spot else 0.0
        if dist_pct < min_distance_pct:
            continue
        if best is None or leg.oi > best[0]:
            best = (leg.oi, row.strike, leg)
    return best


def select_primary(chain, *, side: str | None = None,
                    min_distance_pct: float = DEFAULT_MIN_DISTANCE_PCT,
                    min_oi: float = DEFAULT_MIN_OI,
                    lot_size: int = 1) -> PrimarySelection:
    spot = getattr(chain, "spot", None)
    if not spot or not chain.rows:
        return PrimarySelection(status="NO_TRADE", leg=None, side=None,
                                reason="no spot or no chain rows")

    candidates = {}
    if side in (None, "CE"):
        w = _best_wall(chain, "ce", "CE", spot=spot, min_distance_pct=min_distance_pct, min_oi=min_oi)
        if w:
            candidates["CE"] = w
    if side in (None, "PE"):
        w = _best_wall(chain, "pe", "PE", spot=spot, min_distance_pct=min_distance_pct, min_oi=min_oi)
        if w:
            candidates["PE"] = w

    if not candidates:
        return PrimarySelection(status="NO_TRADE", leg=None, side=None,
                                reason=f"no strike >= {min_distance_pct}% OTM cleared min_oi={min_oi} on either side")

    best_side = max(candidates, key=lambda s: candidates[s][0])
    oi, strike, leg = candidates[best_side]
    primary = PrimaryLeg(strike=strike, option_type=best_side, premium=leg.ltp,
                         delta=leg.delta, lot_size=lot_size)
    return PrimarySelection(status="SELECTED", leg=primary, side=best_side,
                            reason=f"largest qualifying OI wall: {best_side} {strike} (oi={oi})")
