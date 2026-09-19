"""
EXPIRY ZERO TO HERO — research engine.

RESEARCH / PAPER ONLY. Never enables live trading. Contains no order path.

Discovers (does not assume) the mathematics of an extreme option-premium
expansion in the final ~40 minutes before an index option expiry. See
EXPIRY_ZERO_TO_HERO.md for the data-availability matrix, the 03-Sep-2026 SENSEX
validation case, and the current UNCALIBRATED status.
"""
from . import replay
from .backtester import ExpiryZeroToHeroBacktester
from .bs import decompose_move, greeks, implied_vol
from .data_collector import ExpiryDataCollector
from .features import ExpiryFeatureEngine
from .labeler import ZeroToHeroLabeler
from .probability import ZeroToHeroProbabilityEngine
from .signal import ExpiryZeroToHeroReporter, ZeroToHeroSignalEngine
from .support_detector import PremiumSupportDetector

__all__ = [
    "ExpiryDataCollector",
    "ExpiryFeatureEngine",
    "ExpiryZeroToHeroBacktester",
    "ExpiryZeroToHeroReporter",
    "PremiumSupportDetector",
    "ZeroToHeroLabeler",
    "ZeroToHeroProbabilityEngine",
    "ZeroToHeroSignalEngine",
    "decompose_move",
    "greeks",
    "implied_vol",
    "replay",
]
