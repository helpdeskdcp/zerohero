"""
SMART_INDEX_SCALPER — orchestration layer over MATHEMATICAL_CONFLUENCE_ENGINE_V1
(spec slice 2/6).

RESEARCH / PAPER-ANALYSIS ONLY. No order path. live_trading stays false. This
layer ranks the configured index universe and emits a candidate signal; it does
NOT open a paper position (slice 3 = option selection + profile filter over the
existing autoscalp.safeguards + paper_trading).
"""
from .scanner import ENGINE_NAME, SmartIndexScalper
from .universe import DEFAULT_UNIVERSE, index_meta, resolve_universe
from . import eligibility, selection_score

__all__ = [
    "SmartIndexScalper", "ENGINE_NAME", "DEFAULT_UNIVERSE",
    "index_meta", "resolve_universe", "eligibility", "selection_score",
]
