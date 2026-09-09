"""
TRI-COMPARE :: 3-engine research framework -- RESEARCH / BACKTEST ONLY.

Compares three INDEPENDENT strategies on the SAME NIFTY data, timeframe, costs,
slippage, session rules, risk model and walk-forward methodology:

  ENGINE 1 -- CLAUDE TREND ENGINE
      Time-series momentum / trend-following: EMA/MA trend + Donchian breakout,
      ATR volatility-regime filter, anti-whipsaw (persistence + Kaufman
      efficiency ratio + ADX), ATR-based SL, trailing exit, volatility-
      normalized sizing (every trade risks 1R).

  ENGINE 2 -- PRECISION STRUCTURE ENGINE  (the ChatGPT-spec engine)
      STRUCTURE -> LOCATION -> MOMENTUM -> CONFIRMATION -> MATHEMATICAL ENTRY.
      HH/HL + LH/LL, BOS/CHoCH, breakout+pullback, validated S/R, VWAP, EMA/HMA,
      RSI, ADX/DI, candle strength, ATR. ENTRY != SIGNAL: two-stage
      SIGNAL_DETECTED -> WAITING_FOR_RETEST -> ENTRY_READY, no-chase filter,
      rejection/confirmation candle, mathematical RR gate. Stale/invalid/
      no-chase setups are rejected.

  ENGINE 3 -- HYBRID ENGINE
      Engine 1 sets the directional REGIME gate; Engine 2 sets LOCATION + entry
      TIMING. Combined ONLY through independent, explainable gates with
      configurable thresholds -- never a blind score sum.

ANN: the existing hcs adaptive logit is reused as a SEPARATE confirmation layer
(`ann_layer`). Fit TRAIN-only, per walk-forward fold; no future leakage. Each
engine is reported both engine-only (A) and engine+ANN (B). ANN is kept only if
it shows a genuine OOS improvement.

SAFETY: imports nothing from app.autoscalp / execution / main; no broker, no
order path, `live_trading` untouched. Existing engines (orderflow v1, order_
pressure, hcs, ...) are NOT modified -- only imported read-only for reuse.

Test instrument: NIFTY 50 INDEX only (cash-index 1m Kaggle history -> resample;
volume = 0, so volume features are inert and labelled). Options-premium data is
never used for this underlying-index study. BANKNIFTY / SENSEX profiling comes
later, only after a winner is established.

No 99%-win-rate target. No cherry-picking. Rules are frozen before OOS is
scored. If all three fail OOS -> report NO-GO and diagnose, never force a signal.
"""
