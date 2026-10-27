# NXI signal rules (exactly what the code does)

All rules use **closed bars only** (`calc_on_every_tick=false`); a market order fills on the **next bar open**; no value is recomputed or back-dated after the bar closes.

## 1 Sessions, previous day, gap
* New day = first bar of the exchange day. Previous-day O/H/L/C and VWAP/TWAP are the finished day's values, usable only from the next day's first bar.
* Daily ATR = simple mean of the last `Daily ATR window` (14) finished sessions' true ranges, tracked from the chart's own history (needs >= 5 sessions, else gap class `UNKNOWN`).
* `GapPoints = TodayOpen - PrevClose`; `GapPercent = 100 x GapPoints / PrevClose`; `GapATR = GapPoints / dailyATR`.
* Class: `|GapATR| < 0.15` FLAT; `>= 1.00` EXCEPTIONAL UP/DOWN; otherwise GAP UP/DOWN (thresholds are inputs). Exceptional-gap sessions are no-trade by default.

## 2 First candle and the six scenarios
First completed 5-minute candle: body/range, close location `(close-low)/range`, strong bullish = body >= 0.55 and close-loc >= 0.65, strong bearish = body >= 0.55 and close-loc <= 0.35. `fill = (close-open)/(prevClose-open)` = share of the gap closed by that candle.
| # | scenario | provisional at the first candle | validated at the 2nd candle close |
|---|---|---|---|
| 1 | gap-down continuation | gap down + strong bearish first candle | 2nd candle closes below the 1st close and does not exceed its high |
| 2 | gap-down reversal | gap down + strong bullish first candle with fill >= 0.35 | 2nd candle closes above the 1st close and holds its low |
| 3 | gap-up continuation | gap up + strong bullish first candle | mirror of 1's validation (bullish) |
| 4 | gap-up reversal | gap up + strong bearish first candle with fill >= 0.35 | mirror of 2 |
| 5 | flat-open breakout | FLAT class; direction is set by the first **close** beyond the 15-minute opening range (after it completes) | withdrawn if price closes back inside within 6 bars |
| 6 | no-trade / conflicting | everything else, or a scenario the 2nd candle did not validate | - |
The scenario never changes retroactively. Evidence component `gap scenario` = +1/-1 only for a validated scenario (continuation and reversal give the direction of the validated move), else 0.

## 3 Zones (previous-session OI is MANUAL; prices only)
Slots: OI#1, OI#2 (rank weights 1.0 / 0.6), two extra zones (0.4) and the previous-day low (support) / high (resistance) (0.8). Zone = strike +/- half-width (20 pts). Per session each zone counts **respect** events (support: bar low reaches the zone and closes back above it; resistance mirrored) and is **broken** by a close beyond it. A break may be required to be preceded by >= 1 respect ("price-confirmed", default on) - the largest-OI strike is *not* assumed strongest. A broken support whose price later closes back above it is `reclaimed`.

## 4 SMC (all confirmed-pivot based)
* Pivot high = high R(3) bars ago strictly above the L(3) bars to its left and >= the R bars to its right (explicit loops: the same answer in every engine). It becomes known R bars later and is stored at that bar.
* BOS = close beyond the last confirmed swing in the trend direction; CHoCH = close beyond it against the trend. One event per swing.
* Liquidity sweep = high above an unbroken confirmed swing high with a close back below it (low/below for sell-side), once per swing. Failed breakout/breakdown = a close beyond the swing followed by a close back inside within 6 bars. Retest = after a break, price comes back within 0.25 ATR of the level and closes on the break side (<= 8 bars).
* FVG = `low > high[2]` (bull) / `high < low[2]` (bear), known at the third candle's close; mitigated by a later close through it; at most 8 open zones are kept.
* Displacement = range >= 1.2 ATR and body >= 60 % of range.

## 5 Potential seller stress (symmetrical)
* **Put-side (bearish, PE)**: a support zone is broken and price still closes below it; the zone has weight >= 0.35 and (by default) was respected before the break; a rejection from below happened in the last 8 bars (bar reached the zone, closed below it); bearish structure (BOS/CHoCH) or downward displacement is fresh; momentum agrees (RSI < 50 and fast EMA < slow EMA).
* **Call-side (bullish, CE)** is the exact mirror on a resistance zone (a holding retest above it replaces the rejection).
* Label: `POTENTIAL SELLER STRESS`. It is a pattern near a user-supplied positioning zone; it is not evidence of any seller's stop-loss or intent.

## 6 Candle/volume proxy
Six independent conditions: relative volume >= 1.5 (only if the chart has volume), range >= 1.3 ATR, body >= 60 % in the candle's direction, close location extreme, a pre-existing level just broken/retested/swept, fast/slow EMA aligned. Four of six (default) and more than the opposite side -> `bullish`/`bearish impulse`. Shown as `CANDLE/VOLUME PROXY`; it is original and does not reproduce any proprietary indicator.

## 7 Evidence index (a signed score, **not** a probability)
Eleven components, each +1 / -1 / 0: higher-timeframe agreement, gap scenario, level position, opening candle, BOS/CHoCH, sweep/failed break, volume, RSI momentum, VWAP/TWAP side, option-premium confirmation, manual OI/IV evidence. A component that cannot be computed (no volume, no option data, no OI evidence) is **unavailable**: weight 0 in the denominator, 0 in the numerator, and the dashboard shows `-`. `bull = sum(w of +1) / sum(w of available)`, `bear` likewise, `net = bull - bear`. Correlation control: trend (HTF EMAs), momentum (RSI), VWAP and structure are measured from different quantities and never entered twice.
HTF votes use the previous **completed** 15/30/60/240-minute bar (`request.security(..., f()[1], lookahead_on)`): EMA(9) vs EMA(21) and close.

## 8 Entry
Trigger (input): *Stress only* / *Gap scenario only* / *Stress or gap scenario* (default; gap scenario = validated scenario + fresh structure in its direction). Signal on the **rising edge** of: trigger AND `|net| >= 0.35` AND >= 4 non-zero components, with all gates: 5-minute chart, valid ATR/RSI/prev-close, no abnormal bar (> 3 ATR), not exceptional-gap session, time window (after the opening wait, before 14:45), risk caps, and a structure stop within 3 ATR whose distance to the nearest opposing level (zone / previous-day extreme / opening range / swing) is at least 1R. A rising edge that occurs on a blocked bar is **not chased** on later bars.
**Stop** = nearest valid structure at least 0.4 ATR away (fresh sweep extreme, last confirmed swing, the broken zone's edge, opening-range edge) +/- 0.3 ATR. **Targets** 1R / 2R / 3R from the actual fill; 40 % / 30 % / 30 % exits; after T1 the stop moves to entry, after T2 to T1 (optional). Invalidation exits: opposite CHoCH or opposite evidence beyond the threshold, session loss limit, square-off at 15:15.

## 9 Risk
Max 2 trades per session, max session loss (account currency, realised + open), 6-bar cooldown after a loss, no new entries from 14:45, flat at 15:15, no entry before the 2nd candle has closed, abnormal-volatility and bad-data bars blocked. These are simulation rules; real markets can gap through a stop.

## 10 Probability
`PROBABILITY NOT CALIBRATED (n=x/50)` until 50 resolved virtual signals exist per side; then the **running in-sample hit rate** of "Target 1 before stop within 24 bars" with a Wilson 95 % interval, labelled in-sample. It is not an out-of-sample forecast. Offline calibration (chronological split, Brier score, reliability table) is done by `scripts/pine_nifty_report.py`; the numbers are in `NXI_VALIDATION.md`.
