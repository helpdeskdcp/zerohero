"""
Inside-Bar 2-minute breakout scalping strategy -- RESEARCH / BACKTEST ONLY.

Imported by NOTHING in the live app: no runner, HCS, ANN, EPM, signal, order,
SL or target code. Reads existing historical stores; writes only under
backend/data/research/inside_bar/.

Rules (from the user's spec):
  * indices: NIFTY, BANKNIFTY, SENSEX only
  * timeframe: 2-minute bars
  * Inside Bar = bar fully contained in the prior bar: high <= prev.high AND
    low >= prev.low  ("halt bar" -- contraction / pause)
  * context: 20-EMA on the 2m closes. LONG only if close > EMA, SHORT only if
    close < EMA, at the IB.
  * qualifier: the IB must follow a MOMENTUM leg (a directional push in the
    lookback window), not dead chop.
  * trigger: break of the IB high (long) / IB low (short) within 2-3 bars,
    else the setup is void.
  * stop: just past the opposite IB extreme (long -> below IB low; short ->
    above IB high) + a small buffer.
  * targets: R multiples 2 / 3 / 4 (1R = entry-to-stop distance).
  * scale-out: 1/3 at +1R, 1/3 at +2R, trail the last third (breakeven after
    +1R, then trail by 1R steps) up to +4R.
  * limits: <= 4 trades per session; no NEW entry after 12:30 IST; hard flat
    at 13:00 IST.

  config.py     every threshold, one dict, overridable
  data.py       read-only 2m-bar loaders (Kaggle 1m -> 2m; market_history.db)
  strategy.py   EMA + momentum + inside-bar detection + trade lifecycle
  backtest.py   chronological replay + walk-forward split + 17-item report +
                GO/NO-GO from OUT-OF-SAMPLE evidence, + reproducible CLI

SAFETY: no broker calls, no live orders, no production wiring, no changes to
any existing application logic. Missing data is reported, never invented.
"""
