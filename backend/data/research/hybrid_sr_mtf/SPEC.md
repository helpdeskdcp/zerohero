# Hybrid S/R multi-timeframe strategy candidates — mathematical specification (as implemented)
Code: `app/research_engines/hybrid_sr_mtf/{data,engine,sim,metrics}.py`, tests `tests/test_hybrid_sr_mtf.py` (10). RESEARCH ONLY: no order path, no production import. All numbers below are the frozen defaults of `Params`, written before any result was looked at.

## 0. Causality rules (what "no look-ahead" means here)
* Bars: 1-minute INDEX candles (session 09:15–15:29). 5-minute and 15-minute bars are built from the 1-minute bars of the same session; bar k of length K starts at 09:15 + K·j.
* A higher-timeframe bar is usable only at or after its COMPLETION minute (`end = start + K`). A decision at the end of 5-minute bar i uses bars ≤ i (5m) and the last completed 15m bar.
* Swing pivots are fractals (w = 2): bar j is a pivot high if `h[j] > max(h[j-2..j-1])` and `h[j] > max(h[j+1..j+2])`; it is *known* only when bar j+2 completes (stored at index j+2). So a pivot never exists before it could have been seen.
* Entry = OPEN of the 1-minute bar AFTER the trigger bar closes. Stops/targets are evaluated on the following bars only.
* Test: removing all data after day D leaves every decision, entry and exit before D unchanged (`test_decisions_before_day_D_do_not_change_when_the_future_is_removed`, 3 candidates).

## 1. Building blocks
* ATR: Wilder RMA(14) of the true range, on 5m (`ATR5`) and 15m (`ATR15`). EMA: α = 2/(n+1), seeded with the first value. (Identical to the canonical `app/indicators.py`.)
* **15m bias** `b ∈ {+1, 0, −1}`: `b = +1` iff `C15 > EMA20 > EMA50` and `EMA20[i] > EMA20[i−3]`; `b = −1` for the mirror image; otherwise 0.
* **Major levels** known at the decision: confirmed 15m pivot highs/lows of the last 80 bars; previous-session high and low; opening-range high/low (first 15 minutes, from 09:30). **Minor levels**: confirmed 5m pivots of the last 48 bars.
* Index volume does not exist, so **no volume or VWAP filter is possible on the long history** (a true VWAP needs volume; the futures volume exists only since July 2026). A range-expansion rule stands in for "volume expansion" on breakouts and this is a stated limitation, not a finding.

## 2. Candidates (direction side = +1 long call idea / −1 long put idea; all trade the index-equivalent)
**S1 Sweep reversal.** On 5m bar i, with `L` = the highest confirmed 5m pivot high of the previous 48 bars: `H5[i] > L + 0.05·ATR5`, `C5[i] < L`, `C5[i−1] ≤ L` (the sweep happens on this bar) and a major level lies within `0.5·ATR15` of `L`. Short setup; the low-side mirror is the long setup. Allowed unless the 15m bias is strictly against the trade. Stop anchor = the sweep extreme.
**S2 Break and retest.** Breakout bar: `C5[i−1]` on one side of a level and `C5[i] − level ≥ 0.10·ATR5` on the other, range ≥ 1.0·ATR5, body ≥ 60 % of the range, candle in the breakout direction. Within 8 bars: the retest bar touches `level ± 0.10·ATR5` and closes on the breakout side. A close back through `level − 0.10·ATR5` marks the breakout FAILED (false-breakout statistic). Requires the 15m bias strictly in the direction. Stop anchor = the retest extreme.
**S3 Trend pullback.** 5m `EMA20 > EMA50` (long), the last 12 bars' extreme ≥ EMA20 + 2·ATR5, the bar's low ≤ EMA20 + 0.25·ATR5, closes above EMA20 in a bullish candle. Requires the 15m bias in the direction. Stop anchor = the extreme of the last 3 5m bars.

## 3. Trigger (1 minute) and entry
Within 10 minutes of the 5m bar's completion: a 1-minute candle in the trade direction closing beyond the previous 3 bars' extreme and in the outer 40 % of its range. Entry price = the next bar's open. Otherwise the setup expires (`NO_1M_TRIGGER`).

## 4. Stop, targets, scale-out
`SL = anchor ∓ 0.2·ATR5`; risk `r = |entry − SL|`. Opposing levels (all pivots for `ROOM_ALL`, major only for `ROOM_MAJOR`) are those beyond the entry. **T1** = the nearest opposing level at ≥ 1.2·r (if the nearest one is closer than 1.2·r the trade is REJECTED); **T2** = the next level ≥ 0.5·r beyond T1, else `entry ± max(2·ATR15, |T1−entry| + 0.5·r)`; **T3** = `entry ± max(3·r, |T2−entry| + 0.5·r)`. Legs 40 / 30 / 30 %. After T1 the stop moves to the entry; after T2 to T1 (effective from the NEXT bar). Time exit at the open of the 15:15 bar. A bar touching both stop and target counts the STOP first (flagged ambiguous). A gap through the stop fills at the open.

## 5. NO-TRADE filter list (each rejection is counted by reason)
`TIME_WINDOW` (setup completes outside 09:35–14:30) · `INCOMPLETE_BAR` (a 5m bar with <5 or 15m bar with <15 one-minute bars) · `DAY_TRADE_LIMIT` (2 trades/day) · `DAY_LOSS_LIMIT` (2 consecutive losing trades) · `HTF_CONTRADICTION` · `CHOP_RANGE` (S2/S3: the last 20 5m bars span ≤ 2.5·ATR5) · `IN_POSITION` · `COOLDOWN` (10 min) · `STALE_OR_GAPPED_DATA` (a missing minute or ≥5 unchanged closes in the last 10 minutes) · `ENTRY_BEYOND_STOP` · `RISK_TOO_WIDE` (>2.5·ATR5) · `RISK_TOO_NARROW` (<0.4·ATR5) · `NO_ROOM_TO_OPPOSING_LEVEL` (<1.2 R to the nearest opposing level) · `NO_1M_TRIGGER`.
Not testable on index history and therefore enforced only through the cost model: option bid/ask spread and option liquidity.

## 6. Conversion to an option and costs (stated assumptions)
Index points → one lot of an ATM weekly option: `net = Δ·pts·lot − friction`, Δ = 0.5, lot NIFTY 75 / BANKNIFTY 30 / SENSEX 20 (today's lots applied to the whole history), representative ATM premium NIFTY 150 / BANKNIFTY 350 / SENSEX 400. `friction` = the monitored autoscalp cost model (`net_edge._friction_rs`: charges + measured half-spread) + two extra brokerage orders (+₹47) for the 3-leg scale-out. **Theta and gamma are not modelled** (theta would make results worse for a long-option scalp, gamma better for large moves). A real-option replay was NOT run, because no candidate passed the index-level gate in REPORT.md.

## 7. Evaluation protocol (frozen before results)
Splits — NIFTY: DEV 2017-06..2021-12, VALIDATION 2022–2023, OUT-OF-SAMPLE 2024-01..2026-10. BANKNIFTY and SENSEX: DEV 2021-09..2022-12, VALIDATION 2023, OOS 2024-01..2026-10. No parameter is fitted, so walk-forward = the same frozen rules year by year. Two pre-registered variants (`ROOM_ALL`, `ROOM_MAJOR`) × 3 candidates × 3 instruments = 18 series (multiple-testing: at a 90 % interval about 1–2 would clear zero by chance alone).
**Promotion gate** (written before the results): a candidate is recommended only if, on at least 2 of 3 instruments, OOS net per trade has a 90 % day-cluster bootstrap lower bound > 0, VALIDATION net per trade > 0, OOS trades ≥ 100, the MIRROR control is negative, and it is positive in ≥ 2 of 3 regimes.
Control runs: MIRROR (same entry bar, opposite side, mirrored stop/targets); UNFILTERED (HTF, room, chop and time filters off) versus filtered, with a random-deletion control for "do the filters add anything beyond throwing trades away".
