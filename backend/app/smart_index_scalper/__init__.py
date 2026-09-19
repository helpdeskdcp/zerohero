"""
SMART_INDEX_SCALPER — orchestration layer over MATHEMATICAL_CONFLUENCE_ENGINE_V1
(spec slice 2/6).

RESEARCH / PAPER-ANALYSIS ONLY. No order path. live_trading stays false. This
layer ranks the configured index universe and emits a candidate signal; it does
NOT open a paper position (slice 3 = option selection + profile filter over the
existing autoscalp.safeguards + paper_trading).
"""
from . import (
               eligibility,
               historical_context,
               journal,
               option_selector,
               profiles,
               replay_metrics,
               selection_score,
               state_machine,
)
from .paper_engine import SmartScalperPaperEngine
from .replay import SmartScalperReplay
from .scanner import ENGINE_NAME, SmartIndexScalper
from .scheduler import SmartScalperScheduler
from .universe import DEFAULT_UNIVERSE, index_meta, resolve_universe

__all__ = [
               "DEFAULT_UNIVERSE",
               "ENGINE_NAME",
               "SmartIndexScalper",
               "SmartScalperPaperEngine",
               "SmartScalperReplay",
               "SmartScalperScheduler",
               "eligibility",
               "historical_context",
               "index_meta",
               "journal",
               "option_selector",
               "profiles",
               "replay_metrics",
               "resolve_universe",
               "selection_score",
               "state_machine",
]
