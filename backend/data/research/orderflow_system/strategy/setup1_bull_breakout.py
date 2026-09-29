"""Setup 1 -- Bullish breakout (LONG). See strategy/_breakout.py for the
shared FSM (context -> trigger -> confirm -> entry -> stop -> targets ->
invalidation) and strategy/_common.py for why bull/bear share one
implementation instead of two independently-drifting copies."""
from __future__ import annotations

from ._breakout import BreakoutSetup


class Setup1BullBreakout(BreakoutSetup):
    def __init__(self, full_cfg: dict):
        super().__init__(full_cfg, "setup1_bull_breakout", "bull", "SETUP1_BULL_BREAKOUT")
