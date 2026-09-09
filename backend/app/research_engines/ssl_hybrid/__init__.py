"""
SSL Hybrid PRO -- Universal Index :: RESEARCH / BACKTEST ONLY.

A faithful Python port of the user's tested TradingView Pine v6 indicator
("SSL Hybrid PRO - Universal Index"), wrapped in a walk-forward backtest that
reports WIN / LOSS in POINTS.

Imported by NOTHING in the live app -- no runner, HCS, ANN, EPM, signal, order,
SL or target code. Reads existing historical stores only; writes solely under
backend/data/research/ssl_hybrid/.

Signal (unchanged from the Pine):
  strongBull = bullScore >= minConfidence(70) AND sslBull AND aboveBaseline
               AND aboveVWAP AND adxBull
  strongBear = mirror
  buySignal  = strongBull and not strongBull[1]   (fires once per new setup)
  sellSignal = strongBear and not strongBear[1]

Score (0-100, normalised from a 110-pt raw stack): SSL dir 20, SSL cross 10,
baseline 10, EMA200 10, VWAP 10, HMA slope 10, RSI 55/45 10, ADX+DI 10,
volume-with-direction 5, >=50% body candle 5.

Risk engine (unchanged): entry = signal close; SL = entry -/+ ATR(14) * 1.5;
T1/T2/T3 at RR 1 / 2 / 3.

Trade management for the backtest (the Pine only draws levels): 1/3 off at T1
(then SL -> break-even), 1/3 off at T2, last 1/3 at T3 or a session-end flat;
opposite signal also flattens. One position at a time. SL-before-target within
a bar. All configurable in config.py.

DATA NOTE: NSE cash-index history has zero/absent volume, so `aboveVWAP` would
kill every signal. We substitute a session-anchored cumulative HLC3 mean as a
disclosed price-VWAP proxy (== true VWAP under equal per-bar volume). The
volume-score component (5 pts) is therefore inert on this data. Reported, never
hidden.
"""
