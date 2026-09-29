"""Setup 4 -- Bearish reversal (SHORT from top), mirror of Setup 3. See
strategy/_reversal.py for the shared FSM."""
from __future__ import annotations

from ._reversal import ReversalSetup


class Setup4BearReversal(ReversalSetup):
    def __init__(self, full_cfg: dict):
        super().__init__(full_cfg, "setup4_bear_reversal", "bear", "SETUP4_BEAR_REVERSAL")
