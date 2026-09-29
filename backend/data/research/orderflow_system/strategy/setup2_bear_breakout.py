"""Setup 2 -- Bearish breakout (SHORT), mirror of Setup 1. See
strategy/_breakout.py for the shared FSM."""
from __future__ import annotations

from ._breakout import BreakoutSetup


class Setup2BearBreakout(BreakoutSetup):
    def __init__(self, full_cfg: dict):
        super().__init__(full_cfg, "setup2_bear_breakout", "bear", "SETUP2_BEAR_BREAKOUT")
