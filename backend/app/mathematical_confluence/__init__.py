"""
MATHEMATICAL_CONFLUENCE_ENGINE_V1 — evidence-based mathematical + OI confluence.

RESEARCH / PAPER-ANALYSIS ONLY. No order path. live_trading stays false.

Composes existing codebase math (turning_point_engine._pivots,
expiry_zero_to_hero.oi_change) with new Gann-level / confluence-zone /
market-position / 7-sub-score modules. Score weights are configurable and
NOT calibrated until backtested (spec section 13 / 26).
"""
from .engine import ENGINE_NAME, SIGNAL_TYPES, MathematicalConfluenceEngine
from .levels import classical_pivots, gann_levels, normalized_levels
from .confluence import cluster_levels, high_confluence_zones, nearest_zone
from .market_position import classify_regime, market_position
from .oi_confluence import oi_matrix
from .swings import detect_swings, swing_stats
from . import scoring

__all__ = [
    "MathematicalConfluenceEngine", "ENGINE_NAME", "SIGNAL_TYPES",
    "classical_pivots", "gann_levels", "normalized_levels",
    "cluster_levels", "nearest_zone", "high_confluence_zones",
    "market_position", "classify_regime", "oi_matrix",
    "detect_swings", "swing_stats", "scoring",
]
