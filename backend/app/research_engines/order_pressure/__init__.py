"""
Multi-Candle Order-Pressure & Dynamic Trade-Management Engine  --  RESEARCH ONLY.

This package is a self-contained backtest / research engine. It is imported by
NOTHING in the live application: no runner, no HCS, no ANN (adaptive*.py), no
EPM, no signal path, no order / SL / target logic. It only READS the repository's
existing historical stores and writes its own results under
`backend/data/research/order_pressure/`.

  data.py        read-only loaders + coverage_report()  (what exists vs what is missing)
  config.py      every formula constant / threshold, one place, overridable
  formulas.py    candle math + buyer/seller/net pressure (0-100, zero-range safe)
  series.py      pressure slope / acceleration / persistence / reversal-divergence
                 over 1/3/5/10/15 completed candles with recency weighting
  features.py    causal per-bar feature vector: SPOT + CE + PE pressure, OI &
                 OI-change, price/OI state (candidates), multi-TF alignment/conflict
  labels.py      next-candle UP / DOWN / INSIDE
  models.py      baselines + a small regularised 1-hidden-layer MLP (pure Python)
  calibrate.py   per-class isotonic + Brier / ECE
  walkforward.py expanding-window, chronological, no look-ahead
  trade_mgmt.py  per-bar TargetAchievement / SLThreat / Recovery probabilities +
                 early-exit / fixed-SL / trailing-SL simulators (never widen SL)
  backtest.py    orchestrator + reproducible CLI + the 17-item report + GO/NO-GO

SAFETY: no broker calls, no live orders, no production wiring, no changes to any
existing application logic. Missing data is reported, never invented.
"""
