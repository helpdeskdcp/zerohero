# EDGE ARCHITECTURE - final status per engine
Pipeline requested: Regime -> Structure -> Liquidity -> Price action -> Trend -> Volume -> Momentum -> Traps -> Score -> RR validation -> BUY CE / BUY PE / NO TRADE. ZHX v1 already outputs exactly those three states (no sell), candle-close only, cooldown 6, max 3/day, invalidation by SL/EOD, Entry/SL/T1-T3 lines and a dashboard. T4 and trailing SL were not added (they change exits and no evidence supports it).
| Engine | Status | Why |
|---|---|---|
|1 Market regime|INCONCLUSIVE|ADX range-gate helped NIFTY OOS (+105) but hurt SENSEX (-77); not consistent|
|2 Smart money (BOS/FVG/premium-discount)|ACCEPT as is (part of the score)|no ablation of removing it was run; kept unchanged|
|3 Liquidity / stop hunt|INCONCLUSIVE|it is the trigger; gross R about 0 to +0.2, not enough after costs|
|4 Price action|REJECT (extra filters)|close-location filter: no change; body>=1.0 ATR: inconclusive|
|5 Trend|REJECT (extra filters)|15m, 1h and 5m-EMA filters did not help or hurt|
|6 Volume|NOT AVAILABLE|index volume = 0|
|7 Momentum|REJECT|RSI side/exhaustion filters: no benefit|
|8 Options intelligence|NOT AVAILABLE in Pine / UNVALIDATED|Safe AI applies live chain gates; 11 days of history only|
|9 Trap detection|INCONCLUSIVE|the sweep is the trap; no separate failed-retest engine built|
|10 Confluence score|REJECT as a quality measure|score>=7 is much worse (-166) than score>=6 (-61)|
|11 Risk management|ACCEPT (as implemented); T4/trail/daily-loss not built|no evidence|
|12 Historical validation|ACCEPT (done offline, this study)|MFE/MAE, session, regime, CE/PE, yearly|
