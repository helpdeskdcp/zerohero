"""
Requirements 5-7: confirmed breakout detection (close-only, never an
intrabar wick), retest detection (successful/failed), and support<->
resistance flip detection.

Pure, causal state machine: bar i's classification depends only on bars
0..i (the close of bar i, which only exists once that bar has actually
closed) and state accumulated from earlier bars -- never on bar i+1 or
later. Re-running on a longer bar array is therefore guaranteed to
reproduce every already-emitted event unchanged and only ever append new
ones after the point where the two bar arrays diverge (verified by
tests/sr_dynamic/test_breakout.py's growing-history test, the same
methodology used for app.strategy_mtf.htf_resample's anti-repaint tests).

BREAKOUT_UP: price was at/below the zone, then a bar CLOSES above
zone_high + buffer -- the zone was acting as resistance, now broken.
BREAKOUT_DOWN: mirror -- the zone was acting as support, now broken.

After a breakout, a RETEST is price closing back into the zone band. From
there:
  * price continues in the breakout's original direction -> SUCCESSFUL
    retest -> the zone flips role (RESISTANCE_TO_SUPPORT after a break-up,
    SUPPORT_TO_RESISTANCE after a break-down).
  * price closes back through the zone the OPPOSITE way -> FAILED retest
    (also counted as a failed breakout -- the level was reclaimed).
A breakout that gets reclaimed before ever retesting cleanly is also a
failed breakout.
"""
from __future__ import annotations

from dataclasses import dataclass

NONE, PENDING, SUCCESSFUL, FAILED = "NONE", "PENDING", "SUCCESSFUL", "FAILED"
CONFIRMED = "CONFIRMED"
RESISTANCE_TO_SUPPORT = "RESISTANCE_TO_SUPPORT"
SUPPORT_TO_RESISTANCE = "SUPPORT_TO_RESISTANCE"


@dataclass
class BreakoutState:
    breakout_status: str          # NONE | CONFIRMED  (as of the latest bar)
    breakout_direction: str | None   # "UP" | "DOWN" | None
    retest_status: str            # NONE | PENDING | SUCCESSFUL | FAILED
    flip_status: str              # NONE | RESISTANCE_TO_SUPPORT | SUPPORT_TO_RESISTANCE
    breakout_count: int
    successful_retest_count: int
    failed_breakout_count: int


def analyze_breakout_retest_flip(zone_low: float, zone_high: float, C: list[float],
                                 atr: float, *, buffer_atr_mult: float = 0.2) -> BreakoutState:
    if not C or not atr or atr <= 0:
        return BreakoutState(NONE, None, NONE, NONE, 0, 0, 0)

    buf = buffer_atr_mult * atr
    side = None                     # "ABOVE" | "BELOW" | "INSIDE"
    pending = None                  # {"dir": "UP"|"DOWN", "retest_seen": bool}
    breakout_count = successful_retest_count = failed_breakout_count = 0
    cur_breakout_status, cur_direction = NONE, None
    cur_retest_status, cur_flip_status = NONE, NONE

    def _side(px):
        if px > zone_high:
            return "ABOVE"
        if px < zone_low:
            return "BELOW"
        return "INSIDE"

    for px in C:
        if side is None:
            side = _side(px)
            continue

        if pending is None:
            if side in ("ABOVE", "INSIDE") and px < zone_low - buf:
                breakout_count += 1
                pending = {"dir": "DOWN", "retest_seen": False}
                cur_breakout_status, cur_direction = CONFIRMED, "DOWN"
                cur_retest_status, cur_flip_status = PENDING, NONE
                side = "BELOW"
            elif side in ("BELOW", "INSIDE") and px > zone_high + buf:
                breakout_count += 1
                pending = {"dir": "UP", "retest_seen": False}
                cur_breakout_status, cur_direction = CONFIRMED, "UP"
                cur_retest_status, cur_flip_status = PENDING, NONE
                side = "ABOVE"
            else:
                side = _side(px)
            continue

        d = pending["dir"]
        touched = zone_low - buf <= px <= zone_high + buf
        if touched:
            pending["retest_seen"] = True
            cur_retest_status = PENDING
            continue

        reclaimed = (px > zone_high + buf) if d == "DOWN" else (px < zone_low - buf)
        continued = (px < zone_low - buf) if d == "DOWN" else (px > zone_high + buf)

        if pending["retest_seen"] and continued:
            successful_retest_count += 1
            cur_retest_status = SUCCESSFUL
            cur_flip_status = SUPPORT_TO_RESISTANCE if d == "DOWN" else RESISTANCE_TO_SUPPORT
            pending = None
            side = "BELOW" if d == "DOWN" else "ABOVE"
        elif reclaimed:
            failed_breakout_count += 1
            cur_retest_status = FAILED
            pending = None
            side = "ABOVE" if d == "DOWN" else "BELOW"
        # else: still pending (either no retest yet and drifting sideways, or
        # retest seen but hasn't resolved either way yet) -- leave state as is

    return BreakoutState(
        breakout_status=cur_breakout_status, breakout_direction=cur_direction,
        retest_status=cur_retest_status, flip_status=cur_flip_status,
        breakout_count=breakout_count, successful_retest_count=successful_retest_count,
        failed_breakout_count=failed_breakout_count,
    )
