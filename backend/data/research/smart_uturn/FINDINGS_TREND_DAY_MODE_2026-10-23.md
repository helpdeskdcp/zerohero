# Trend-day (breakout) mode — findings (2026-10-23)
Spec committed first (SPEC_TREND_DAY_MODE_2026-10-23.md). Kaggle NIFTY 1-minute, same config, 2 points per round trip. A first run of BREAKOUT returned 0 trades because of an ADX scaling bug in my mirror (0-1 instead of 0-100, found by a debug count); fixed and re-run, PULLBACK numbers unchanged (regression check).
| period | chart | mode | trades | gross R | net R | net 95% CI |
|---|---|---|---|---|---|---|
| 2015-19 | 5m | BREAKOUT | 545 | +0.095 | -0.020 | -0.121..+0.082 |
| 2015-19 | 15m | BREAKOUT | 329 | +0.089 | +0.020 | -0.079..+0.122 |
| 2020-25 | 5m | BREAKOUT | 662 | +0.149 | +0.092 | +0.005..+0.177 |
| 2020-25 | 15m | BREAKOUT | 345 | +0.076 | +0.040 | -0.047..+0.132 |
| 2015-19 / 20-25 | 5m / 15m | BOTH | 919-2027 | +0.016..+0.068 | -0.065..-0.015 | all include 0 |
Pullback (shipped) for reference: gross +0.046..+0.093 (5m), about 0 (15m); net -0.05..-0.02.
Verdict (pre-registered rule: all four cells need a net CI lower bound above 0): NO EDGE. BREAKOUT is the most promising variant so far: gross positive in all four cells, net positive in three, one cell (2020-25, 5m) with an interval above 0. With four cells and one modest hit this is not validation; the dilution in BOTH shows signals do not simply add. Not tested on the 1-minute chart (tested on 5m and 15m).
## 2026-10-23 rally check (1-minute chart, Upstox data of the day)
All seven breakout conditions held together on only 5 bars (about 13:38-13:43, the vertical jump 22425 -> 22565, ADX 23-35, RSI 80). The mirror still produced no trade: on those bars the swing-low stop was about 7 ATR away, so the "skip if the stop is wider than 2.5 ATR" rule rejected the entry; entering would have meant buying after +90 points with a 120-point stop. The rally was therefore missed by design, not by a bug; a lagging trend filter and a wide-stop cap both keep the script out of vertical spikes.
Ship decision: optional input "Entry mode" (Pullback only / Trend-day only / Both), default Pullback only, labelled tested-negative.
