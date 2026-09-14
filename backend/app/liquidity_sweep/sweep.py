"""
Liquidity sweep detection -- section 4: price takes liquidity beyond a
level, wicks/rejects, and a completed candle closes back inside, within a
short reclaim window (matches app.orderflow.h1h7_state's own
"reclaimed_within_3" convention for the same underlying idea -- a sweep
that takes many bars to reclaim isn't the same pattern as a sharp
stop-run, so an unbounded window would blur two different things).

Scans BACKWARD from the end of `bars` (already truncated to <= T) for a
sweep whose reclaim has ALREADY happened inside the supplied bars -- never
looks forward, so a call with bars up to T can only ever report a sweep
that was already fully confirmed by T. This is the single most
look-ahead-sensitive function in the whole package; see
tests/test_liquidity_sweep_lookahead.py for the mutation test on it
specifically.

A plain breakout (wick beyond, no reclaim) is explicitly NOT a sweep --
returns None, exactly per the brief's "do not classify every breakout as a
sweep."
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

UPPER_SWEEP, LOWER_SWEEP = "UPPER_SWEEP", "LOWER_SWEEP"


@dataclass
class Sweep:
    kind: str                # UPPER_SWEEP | LOWER_SWEEP
    level: float
    level_source: str        # "PDH" | "PDL" | "EQUAL_HIGH" | "EQUAL_LOW" | caller-supplied
    sweep_extreme: float      # the wick that took liquidity
    sweep_bar_index: int      # index (within the bars passed in) of the wick bar
    reclaim_bar_index: int    # index of the bar that closed back inside
    bars_to_reclaim: int
    reaction: float           # how far beyond the level the wick travelled

    def to_dict(self) -> dict:
        return asdict(self)


def detect_sweep(bars: list[dict], levels: list[dict], *, reclaim_window: int = 3,
                  min_reaction_atr: float | None = None, atr: float | None = None) -> Sweep | None:
    """`levels`: [{"price": float, "source": str}, ...]. Returns the most
    recently CONFIRMED sweep (reclaim bar is the last bar in `bars`), or
    None. `min_reaction_atr`+`atr`: optional filter so a 1-tick poke through
    a level doesn't count as a "meaningful reaction" -- both must be given
    together or neither is applied."""
    n = len(bars)
    if n < 2 or not levels:
        return None
    reclaim_idx = n - 1
    reclaim_bar = bars[reclaim_idx]
    earliest_sweep_idx = max(0, reclaim_idx - reclaim_window)

    best: Sweep | None = None
    for lvl in levels:
        level_price, source = lvl["price"], lvl.get("source", "LEVEL")
        for sweep_idx in range(reclaim_idx, earliest_sweep_idx - 1, -1):
            bar = bars[sweep_idx]
            # UPPER: this bar's wick exceeded the level
            if bar["h"] > level_price:
                reaction = bar["h"] - level_price
                if min_reaction_atr is not None and atr:
                    if reaction < min_reaction_atr * atr:
                        continue
                # reclaim: the LAST bar closes back below the level (same
                # bar counts too, when sweep_idx == reclaim_idx)
                if reclaim_bar["c"] < level_price:
                    cand = Sweep(UPPER_SWEEP, level_price, source, bar["h"], sweep_idx,
                                reclaim_idx, reclaim_idx - sweep_idx, round(reaction, 4))
                    if best is None or cand.reaction > best.reaction:
                        best = cand
            # LOWER: this bar's wick exceeded the level to the downside
            if bar["l"] < level_price:
                reaction = level_price - bar["l"]
                if min_reaction_atr is not None and atr:
                    if reaction < min_reaction_atr * atr:
                        continue
                if reclaim_bar["c"] > level_price:
                    cand = Sweep(LOWER_SWEEP, level_price, source, bar["l"], sweep_idx,
                                reclaim_idx, reclaim_idx - sweep_idx, round(reaction, 4))
                    if best is None or cand.reaction > best.reaction:
                        best = cand
    return best
