# Smart U-Turn port vs the owner's TradingView chart — parity findings (2026-10-23)
Sources: three TradingView screenshots (dashboard at ~13:04, indicator inputs, dashboard at ~13:15) and complete Upstox NIFTY 1-minute data for 2026-10-01..23 (pulled for this check, deleted afterwards).

## Inputs
Every input visible in the screenshots equals the Pine default (RSI 14, swing 8, min score 3, wick 1.3, strict trend, 1 vote, volume off/20, cooldown 3, ATR 14, SL buffer 0.2, min SL 0.5, 1R/2R/3R). Fast/Slow EMA were not visible in the dialog; no EMA pair in a 9x9 grid reproduces TradingView's signals, so they are very likely default.

## What matches exactly
- Dashboard state at 13:04: TF-A/B/C turn and trend cells (no turn/neutral, no turn/neutral, UP TURN/bearish), vote 0 UP / 1 DOWN, BEAR TREND, SELL ACTIVE, structure WAIT / RANGE.
- SL Hunt prices (confirmed pivots): 22431.95 / 22461.35 / 22477.00 at 13:04, and the third column 22461.35 at 13:15.
- SELL labels at 11:44, 11:48, 12:15, 12:37, 13:02 (and 13:14 at the second snapshot) and WAIT circles at 11:36, 11:51, 11:55, 12:25, 12:35; entry within 0.15-0.25 points (feed difference).

## Pine behaviour found and now reproduced (a defect of the original script, kept because it is what TradingView does)
`ta.lowest(low, swingLen)` / `ta.highest(high, swingLen)` are called INSIDE `if (buySignal or sellSignal)`. Pine then evaluates them only on signal bars, so the "8-bar swing" is really the extreme of the last 8 SIGNAL bars (hours apart on a 1-minute chart), not the last 8 candles. Evidence: at the 13:02 SELL TradingView shows resistance about 22471.7, support about 22408 and stop 22474.60. The highest high / lowest low over the last 8 signal bars [11:48, 12:15, 12:20, 12:24, 12:30, 12:37, 12:41, 13:02] are 22472.10 and 22408.50, and 22472.10 + 0.2*ATR(12.4) = 22474.58. The second snapshot (stop 22463.70, resistance 22461.35 = the 12:20 high) fits the same window. Consequence for the owner: real stops/targets on TradingView are much wider than the "8 candles" intent (risk 45-60 points on the 1m chart instead of about 18), and they change with the signal history.
Port: `SignalParams.swing_window = "PINE_CONDITIONAL"` (default, TradingView behaviour) or `"BARS"` (the evident intent). Tested (`test_pine_conditional_window_levels...`).

## NOT resolved
TradingView shows four SELL labels that the port does not produce (about 12:20, 12:24, 12:30, 12:41); at those bars the port's bearish score is 2 (minimum 3) and no component is borderline except a wick ratio of 1.16 vs 1.3 at 12:30. They are real signals of the plotting script (the level window above only matches when they are included). Possible causes: a different TradingView data stream, a Pine evaluation detail not reproduced, or the pasted code differing from the chart's script. Until explained, port parity is PARTIAL, not verified, and the port's level window (fewer signals) is wider than TradingView's. Fastest way to close it: export TradingView's BUY/SELL list or show the same bars' component values.

## Replays re-run with the TradingView window (Kaggle NIFTY 1-minute, 5m and 15m, same pre-registered config)
| period | chart | trades | gross pts | net pts (2 pts/trade) | net expectancy R | 95% CI |
|---|---|---|---|---|---|---|
| 2015-19 | 5m | 1286 | +2068 | -504 | -0.026 | -0.059..+0.008 |
| 2015-19 | 15m | 746 | -1414 | -2906 | -0.019 | -0.048..+0.010 |
| 2020-25 | 5m | 1593 | -4264 | -7450 | -0.045 | -0.075..-0.015 |
| 2020-25 | 15m | 1010 | -843 | -2863 | -0.000 | -0.026..+0.026 |
Still NO EDGE by the pre-registered rule. R values shrink because the wider stops enlarge R; compare points, not R, across the two window readings. The Upstox 4-month option-premium replay was run with the BARS window and was not repeated (its 6.7 GB data was deleted as instructed).
