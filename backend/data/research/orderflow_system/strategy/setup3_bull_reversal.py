"""Setup 3 -- Bullish reversal (LONG from bottom). See
strategy/_reversal.py for the shared FSM (extended-trend overshoot ->
reclaim -> absorption+divergence+delta-flip confirm -> entry -> stop ->
targets)."""
from __future__ import annotations

from ._reversal import ReversalSetup


class Setup3BullReversal(ReversalSetup):
    def __init__(self, full_cfg: dict):
        super().__init__(full_cfg, "setup3_bull_reversal", "bull", "SETUP3_BULL_REVERSAL")
