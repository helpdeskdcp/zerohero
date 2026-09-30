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

from . import risk as _risk
from .selector import PrimaryLeg

DEFAULT_MIN_DISTANCE_PCT = 1.5   # OTM distance from spot, % of spot
DEFAULT_MIN_OI = 500
DEFAULT_MIN_VOLUME = 100         # matches selector.HedgeConfig's own default -- same bar, both legs
DEFAULT_MAX_SPREAD_PCT = 0.15


@dataclass
class PrimarySelection:
    status: str                    # "SELECTED" | "NO_TRADE"
    leg: PrimaryLeg | None
    side: str | None
    reason: str


def _best_wall(chain, side_attr: str, option_type: str, *, spot: float,
               min_distance_pct: float, min_oi: float,
               min_volume: float, max_spread_pct: float):
    """A valid primary (SOLD) leg is validated exactly as rigorously as the
    hedge leg -- same bid/spread/volume/OI bar (selector.py's
    evaluate_candidates()) -- a valid hedge does not make an invalid
    primary valid, and vice versa. Previously this only checked ltp+oi,
    silently letting a wide-spread/thin-volume/no-bid strike become the
    sold leg while the hedge side got the full check (MCP 3.1.2 audit gap:
    "asymmetric leg vetting")."""
    best = None
    for row in chain.rows or []:
        leg = getattr(row, side_attr, None)
        if leg is None or leg.ltp is None or leg.oi is None:
            continue
        if leg.oi < min_oi:
            continue
        if leg.volume is None or leg.volume < min_volume:
            continue
        sp = _risk.spread_pct(leg.bid, leg.ask)
        if sp is None or sp > max_spread_pct:
            continue
        if leg.bid is None:
            continue   # SELL reference price must exist -- never fabricated from ltp
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
                    lot_size: int = 1,
                    min_volume: float = DEFAULT_MIN_VOLUME,
                    max_spread_pct: float = DEFAULT_MAX_SPREAD_PCT) -> PrimarySelection:
    spot = getattr(chain, "spot", None)
    if not spot or not chain.rows:
        return PrimarySelection(status="NO_TRADE", leg=None, side=None,
                                reason="no spot or no chain rows")

    kw = dict(spot=spot, min_distance_pct=min_distance_pct, min_oi=min_oi,
             min_volume=min_volume, max_spread_pct=max_spread_pct)
    candidates = {}
    if side in (None, "CE"):
        w = _best_wall(chain, "ce", "CE", **kw)
        if w:
            candidates["CE"] = w
    if side in (None, "PE"):
        w = _best_wall(chain, "pe", "PE", **kw)
        if w:
            candidates["PE"] = w

    if not candidates:
        return PrimarySelection(status="NO_TRADE", leg=None, side=None,
                                reason=f"no strike >= {min_distance_pct}% OTM cleared min_oi={min_oi} on either side")

    best_side = max(candidates, key=lambda s: candidates[s][0])
    oi, strike, leg = candidates[best_side]
    # This leg is SOLD -- the realistic executable fill is the BID, never
    # the LTP. _best_wall() already requires leg.bid to be present for any
    # wall considered here, so this is never None at this point.
    primary = PrimaryLeg(strike=strike, option_type=best_side, premium=leg.bid,
                         delta=leg.delta, lot_size=lot_size,
                         ltp=leg.ltp, bid=leg.bid, ask=leg.ask)
    return PrimarySelection(status="SELECTED", leg=primary, side=best_side,
                            reason=f"largest qualifying OI wall: {best_side} {strike} (oi={oi})")
