"""
Research-only external-signal reverse-engineering infrastructure
(ZEROHERO reverse-engineering spec, Phases 3-12).

DISCONNECTED BY DESIGN: nothing in this package is imported by, or imports,
app.engines.scalp_strategy, app.autoscalp.runner, app.ai.shadow, or any
broker-execution path. It reads from its own SQLite store
(data/research_events.db) and, read-only, from the existing capture DBs
(data/market_history.db, data/l2_capture.db via app.orderflow.depth). It
never writes to chanakya.db or market_history.db.

Current dataset size is n=5 known external signals (see
data/research/reverse_engineering/PHASE0_PHASE1_REPORT.md) -- far below
any threshold for statistical claims, model training, or production engine
changes. This package exists to make FUTURE research valid once more real
data exists; it draws no conclusions from the current dataset.
"""
