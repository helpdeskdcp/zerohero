"""
Requirement (live wiring spec, section 4): visit-based touch/rejection state
for the CURRENT moment -- "one visit to a zone counts as ONE touch; after
price leaves and later returns, count another touch." This is deliberately
NOT the same convention as touches.analyze_touches (which counts every bar
whose range overlaps the zone -- correct for the backtest/scoring
aggregate touch COUNT, but wrong for a live boolean "is this a new touch or
still the same visit").

Causal: walks bars in order, a bar's classification depends only on bars
0..i. Rejection needs `confirmation_bars` bars of subsequent data to
resolve, exactly like app.sr_dynamic.breakout -- so the LATEST 1-2 bars
of a real visit may legitimately report "pending", never a fabricated
early answer.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TouchState:
    touching: bool                 # is price inside the zone as of the LAST bar
    just_touched: bool             # did THIS bar start a new visit (was outside, now inside)
    rejected: bool                 # did the most recently RESOLVED visit end in a rejection
    rejection_pending: bool        # currently inside/just-exited a visit, not yet resolved
    bars_since_last_touch: int | None
    visit_count: int               # total number of distinct visits (not bar-overlap count)


def latest_touch_state(zone_low: float, zone_high: float, H: list, L: list, C: list,
                       atr: float, *, confirmation_bars: int = 2) -> TouchState:
    if not C or not atr or atr <= 0:
        return TouchState(False, False, False, False, None, 0)

    lo, hi = zone_low - 0.15 * atr, zone_high + 0.15 * atr
    level = (zone_low + zone_high) / 2.0
    n = len(C)

    visit_count = 0
    last_touch_index = None
    last_resolved_rejection = False
    inside = False
    just_touched_flags = [False] * n

    for i in range(n):
        now_inside = L[i] <= hi and H[i] >= lo
        if now_inside and not inside:
            visit_count += 1
            last_touch_index = i
            just_touched_flags[i] = True
            # resolve this visit's rejection using up to confirmation_bars
            # bars strictly after it -- never bars beyond what exists yet.
            resolved = False
            for j in range(i + 1, min(i + 1 + confirmation_bars, n)):
                if abs(C[j] - level) >= 0.3 * atr:
                    last_resolved_rejection = True
                    resolved = True
                    break
            if not resolved:
                last_resolved_rejection = False
        inside = now_inside

    touching = inside
    just_touched = just_touched_flags[-1] if n else False
    bars_since = (n - 1 - last_touch_index) if last_touch_index is not None else None

    rejection_pending = False
    rejected = False
    if last_touch_index is not None:
        bars_available_since = n - 1 - last_touch_index
        if touching:
            rejection_pending = True                 # still inside -- not resolved yet
        elif last_resolved_rejection:
            rejected = True                          # reaction already seen -- resolved now,
                                                       # regardless of how many bars that took
        elif bars_available_since < confirmation_bars:
            rejection_pending = True                 # exited, but not enough bars yet to
                                                       # rule a rejection out for good
        else:
            rejected = False                          # full window passed, no reaction -> genuinely not a rejection

    return TouchState(touching=touching, just_touched=just_touched, rejected=rejected,
                      rejection_pending=rejection_pending, bars_since_last_touch=bars_since,
                      visit_count=visit_count)
