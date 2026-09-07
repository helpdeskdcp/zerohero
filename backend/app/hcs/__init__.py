"""
HCS -- High-Confidence Signal meta-engine.  SHADOW / READ-ONLY.

A quality bar ON TOP of the existing scalping pipeline
(`app/engines/scalp_strategy.decide_from_context`, already calibrated as far as
the data allows). HCS does NOT re-implement modules 1-13 -- it reads the live
engine's own persisted decision (`live_market_snapshots`), maps it onto the 16
requested evidence modules, adds the few genuinely-missing ones (setup memory,
a dedicated exhaustion/liquidity veto), computes a transparent 0-100 quality
score (NOT a probability), applies a hard-filter veto layer that can force
NO_TRADE over any score, and emits an A+ / NO-TRADE verdict with full
per-component + per-veto reasons.

Emits no order, no live signal. Not imported by autoscalp. `live_trading` stays
false. See backend/HCS_ENGINE.md.
"""
from .engine import evaluate  # noqa: F401

__all__ = ["evaluate"]
