"""
Confirmed swing high/low detection for the Dynamic SR Engine.

Reuses app.engines.sr_engine._swings directly (the existing engine's
fractal pivot detector — an established cross-module reuse pattern already
used elsewhere, e.g. app.engines.state_classifier importing _swings for its
own HTF-swing candidates). Not re-implemented here.

Anti-repaint guarantee (already built into _swings, verified by
tests/sr_dynamic/test_pivots.py): a swing at bar index i requires `right`
bars strictly AFTER i to confirm it (the fractal window). _swings' own loop
bound `range(lo, n - right)` means a pivot within `right` bars of the end of
the array is never returned — it isn't confirmable yet from data on hand.
Feeding only CLOSED bars (which every caller in this codebase already does,
via CandleAggregator's closed_only=True) means this is live-mode-safe:
no swing is ever reported before enough real subsequent bars exist to
confirm it, and re-running on a longer bar array never changes a
previously-confirmed swing's index/price/kind.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..engines.sr_engine import _bars, _swings


@dataclass(frozen=True)
class SwingPivot:
    index: int
    price: float
    kind: str        # "H" | "L"


def confirmed_swings(bars: list, *, left: int = 2, right: int = 2, lookback: int = 80) -> list[SwingPivot]:
    """Confirmed fractal swing highs/lows from a closed-bar series.

    `bars`: [{o,h,l,c,v}] or [[t,o,h,l,c,v]], oldest-first, CLOSED bars only.
    Returns [] if fewer than `left+right+1` bars are available -- there is
    nothing to confirm yet, not an error.
    """
    H, L, C, V, n = _bars(bars)
    if n < left + right + 1:
        return []
    return [SwingPivot(index=i, price=p, kind=k) for i, p, k in _swings(H, L, left=left, right=right, lookback=lookback)]
