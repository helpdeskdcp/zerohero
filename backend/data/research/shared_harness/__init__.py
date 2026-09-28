"""Shared backtest harness for data/research/*/ strategy hypotheses.

Consolidates logic that was independently copy-pasted across today's 4
strategy backtests (reversal_sweep, vwap_ema_scalp, false_breakout_reversal,
cpr_orb): candle loading (2 real historical CSV formats + broker-captured
tick resampling), no-look-ahead prior-period levels, standardized SL/TP/EOD
trade simulation, the overlap-safe scan-index rule (the exact bug found and
fixed in reversal_sweep_2026-09), daily trade-management limits, real-cost
bridging via app.institutional_edge.costs, and a consistent VERDICT.md
report shape.

This does NOT retroactively rewrite the 4 existing scripts (each already
ran, was reported, and its VERDICT.md is the record of what was actually
tested) -- it's here so the NEXT hypothesis reuses tested code instead of a
5th hand-rolled copy.
"""
