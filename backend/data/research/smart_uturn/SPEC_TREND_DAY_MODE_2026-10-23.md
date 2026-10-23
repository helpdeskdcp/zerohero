# Smart Trend-Pullback: optional TREND-DAY (breakout) mode — spec, pre-registered 2026-10-23
Trigger: on 2026-10-23 (13:30-14:00 IST) NIFTY rallied ~115 points while both HTFs were still DOWN; the pullback script, which only trades with the HTF trend, gave no BUY. Question: does a rule that ignores the lagging HTF filter when the LOCAL trend is strong add value, and does it hold over 11 years? Fixed now, not tuned afterwards.
## Rule (mode BREAKOUT; no HTF filter)
Long: ADX(14) >= 25, DI+ > DI-, EMA21 > EMA50, close > EMA21, high above the highest high of the previous 20 bars, close > open, RSI(14) >= 55. Short: mirror (ADX >= 25, DI- > DI+, EMA21 < EMA50, close < EMA21, low below the lowest low of the previous 20 bars, close < open, RSI <= 45). Everything else is identical to the pullback script (session window, ATR minimum, cooldown 5, max 4 signals/day, stop after 2 losing trades, 8-bar swing stop +/- 0.2 ATR, min 0.5 ATR, skip if wider than 2.5 ATR, 1R/2R/3R thirds, stop first, flat 15:15, one trade at a time, entry = signal close, cost 2 points).
Modes compared: PULLBACK (the shipped logic, results already in FINDINGS_TREND_PULLBACK), BREAKOUT, BOTH (a signal if either fires).
## Data / charts
Kaggle NIFTY 1-minute, P1 2015-2019, P2 2020-2025; charts 5m (HTFs 15m/60m) and 15m (HTFs 60m/240m), as before.
## Verdict (decided now)
A mode is EDGE only if, for BOTH charts and BOTH periods, n >= 30 and the 95% bootstrap interval (2000 resamples, seed 7) of NET mean R per trade has a lower bound above 0. Otherwise NO EDGE. Descriptive only: win rate, T1/T2/T3 reach rates, gross R, trades per day. If a mode is NO EDGE it ships (if at all) OFF by default and labelled as tested-negative.
Also checked, descriptive: whether BREAKOUT would have produced a BUY in the 2026-10-23 rally (Upstox 1-minute data of that day, deleted after).
