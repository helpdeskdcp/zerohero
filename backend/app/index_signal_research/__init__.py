"""
Index-First Signal Research -- a clearly SEPARATE research area from
`app.liquidity_sweep`, per the pivot mission's explicit instruction not to
touch, retune, or resurrect that package.

`app.liquidity_sweep` STATUS: BASELINE / FAILED HYPOTHESIS (frozen). Real
2-year Kaggle NIFTY 5m backtest, 2,110 signals, chronological TRAIN/VAL/OOS:
OOS directional accuracy 48.1% (coin-flip), WIN/LOSS setup-score means
identical (57.5 vs 57.5), 0/38 features individually discriminative, joint
logistic+forest model OOS AUC 0.541 vs a pre-registered 0.57 pass bar (does
not pass). See `backend/LIQUIDITY_SWEEP_FINAL_REPORT.md` and
`backend/LIQUIDITY_SWEEP_PHASE1_FEATURE_ANALYSIS.md`. Not modified, deleted,
or re-tuned by anything in this package.

This package's objective: find out whether a DIFFERENT, simpler,
measurable index-direction hypothesis (trend continuation, breakout, mean
reversion, pullback-in-trend, opening range, volatility-regime transition,
multi-timeframe structure) carries genuine out-of-sample predictive
information -- honestly measured, not assumed, and only ONE candidate (if
any) gets carried into deeper calibration/ablation/regime work, per the
mission's own "don't build elaborate machinery before finding an edge"
instruction.

Modules:
  data.py        -- real-data loaders (Kaggle NIFTY/BANKNIFTY 5m), with a
                     documented BANKNIFTY exact-duplicate-row fix found
                     during this mission's Phase 1 audit.
  labels.py       -- the UP/DOWN/RANGE outcome definition, registered here
                     BEFORE any hypothesis is evaluated (Phase 4's own rule).
  features.py     -- causal (rolling/shift, never look-ahead) technical
                     features shared across hypotheses.
  hypotheses.py   -- seven distinct, independently falsifiable signal
                     hypotheses (A-G per the mission brief).
  evaluate.py     -- chronological TRAIN/VALIDATION/OOS evaluation,
                     baseline comparison, per-year/per-regime stability.
"""
