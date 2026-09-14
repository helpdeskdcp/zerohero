"""
Liquidity Sweep Intraday Options Strategy -- INDEX-FIRST architecture.

RESEARCH / PAPER ONLY. No order path, no broker call, no live wiring.
Every function in this package takes bars ALREADY TRUNCATED to <= the
evaluation timestamp T by the caller -- the same causal-replay convention
already used by app.orderflow.h1h7_state and app.structural_break (never
re-litigated here, just followed).

Pipeline (index-first, per the user's own explicit redesign mid-build):

  INDEX bars (multi-timeframe)
    -> structure.py    : PDH/PDL, swings, equal highs/lows, HTF bias
    -> sweep.py         : liquidity sweep detection (upper/lower)
    -> confirmation.py  : CHoCH/BOS + CISD/FVG/Order-Block (execution TF)
    -> setup_score.py   : one explainable 0-100 score, reason codes
    -> probability.py   : calibrated P(UP)/P(DOWN)/P(RANGE) + confidence
                           (reuses app.backtest.calibration -- fit/predict/
                           reliability_curve -- not a new calibration method)
    -> risk.py           : SL / 2R target / position sizing (wraps the
                           EXISTING app.engines.risk_engine.run_risk_engine)
    -> strikes.py        : ITM strike selection + ranking (extends
                           app.engines.option_engine's per-leg analytics
                           with an ITM+delta-band constraint that engine
                           doesn't have)
    -> engine.py         : orchestrates the above into the section-25 JSON
                           signal contract

backtest.py runs a TWO-STAGE backtest, kept strictly separate per the
user's own instruction:
  STAGE 1 -- index direction + probability calibration, on REAL Kaggle
             NIFTY/BankNifty 5m OHLC (2014-2026, ~600K bars: genuine
             walk-forward/OOS statistical power).
  STAGE 2 -- option execution (ITM strike, option P&L), on the REAL
             10-day captured option-chain window (market_history.db,
             2026-09-02..11) -- explicitly reported as data-limited,
             never as a profitability claim.
"""
