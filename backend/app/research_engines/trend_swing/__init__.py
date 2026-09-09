"""
TREND-SWING HARNESS -- daily, overnight-holding test of the time-series
momentum / trend-following hypothesis.  RESEARCH / BACKTEST ONLY.

Why this exists: every intraday engine in this project (SSL Hybrid, orderflow
v1, tri_compare E1) showed the trend edge getting *less bad* as the timeframe
slowed (15m -0.21R -> 1h ~break-even). TSMOM in the literature is a daily-to-
monthly phenomenon. This harness tests it on its native horizon:

  * DAILY bars (NIFTY 1m Kaggle -> one bar per IST session date)
  * positions HELD ACROSS DAYS -- no intraday session flat, no "no entry after"
  * direction from {N-day momentum sign, EMA stack, Donchian breakout} agreeing,
    gated by an ATR volatility band + an anti-whipsaw stack
  * entry at the NEXT bar's open (no look-ahead), vol-normalised (risk 1R/trade)
  * exit = hard ATR stop OR a chandelier/Donchian TRAILING stop OR a trend flip
  * proper daily metrics: annualised Sharpe/Sortino, equity-curve max drawdown,
    exposure, avg holding days, CAGR-in-R, by year, by regime
  * CRISIS-WINDOW diagnostic -- TSMOM's core claim is "crisis alpha": does it
    make money during the worst NIFTX peak-to-trough declines?
  * TRAIN -> VALIDATION -> OOS (chronological) + expanding walk-forward; rules
    frozen before OOS; no OOS optimisation; no win-rate target.

SAFETY: imports nothing from app.autoscalp / execution / main / connectors; no
broker, no order path, `live_trading` untouched. Existing research engines are
NOT modified -- `orderflow.indicators` is imported read-only for reuse.

Test instrument: NIFTY 50 INDEX first. BANKNIFTY / SENSEX later, only if a
winner is established. Options-premium data is never used.
"""
