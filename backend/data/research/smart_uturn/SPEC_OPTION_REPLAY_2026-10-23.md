# Smart U-Turn v2.1 — OPTION-premium replay, pre-registered 2026-10-23

Written before the option-level replay was run (the Upstox option pull was still finishing). Index-level result already known: no edge (see SPEC_REPLAY_2026-10-23.md and results_*.json). This asks the question the owner's real trades pose: do the same signals make money when traded as ATM options?

## Data
Upstox expired NIFTY weekly option 1-minute bars (ATM +/- 5 strikes of each expiry, OHLC + volume + OI; NO bid/ask) for expiries 2026-06-23 .. 2026-10-20, and Upstox NIFTY index 1-minute candles over the same dates. Both deleted after the run, as the owner instructed. Window: about 4 months, one regime: the result can only be INSUFFICIENT_SAMPLE or NO EDGE for the period, never validation.

## Signals and index levels
Exactly the Python port with Pine defaults (chart 5 and 15, both reported). Stop, T1, T2, T3 are the Pine index levels computed at the signal bar close. Signal known at the close of its chart bar.

## Option instrument (fixed)
BUY signal -> long ATM CALL; SELL signal -> long ATM PUT (premium buying only, no writing, no spreads). Expiry = the nearest captured weekly expiry with date AFTER the signal date (DTE >= 1; on an expiry day the next week's expiry is used). Strike = the strike nearest to the index close of the signal bar, accepted only if it exists in the data and |strike - index close| <= 75. Anything else is a counted rejection (`contract_not_in_data`), never a substitute contract.

## Execution on 1-minute data (fixed; conservative)
- Entry: the first option bar at or after the minute following the signal bar's close (within 5 minutes, else rejection `no_option_quote`); price = that bar's OPEN plus slippage s.
- Index trigger logic on index 1-minute bars from the entry minute: a long (call) trade stops when the index low <= stop, targets T1/T2/T3 when the index high >= target (mirror for puts). A minute touching the stop and any target counts the stop first. A gap open through the stop exits at that minute. Entry is refused (`entry_gap_through_stop`, `entry_gap_beyond_T1`) when the index entry-minute open is already through the stop or past T1.
- Exit price for each leg = OPEN of the first option bar at or after the minute AFTER the trigger minute (one-minute reaction latency; within 5 minutes, else the trade is rejected as `no_option_quote`) minus slippage s. Session end: the CLOSE of the 15:29 option bar (or last bar of the day) minus s.
- THIRDS: 1/3 of the position at each of T1, T2, T3; the rest stops at the original stop. One position at a time (a signal while a position is open is rejected `position_open`).
- Quantity: 1 lot = 65 (repo `instrument_profiles`, verified); results reported per lot.
- Costs: slippage s = 1.5 percent of the option price per side (stands in for the bid/ask spread, which this data lacks), brokerage Rs 20 per order (entry + each exit leg), and 0.15 percent of sell value for STT/exchange/GST. All are assumptions, not measurements. Sensitivity (descriptive): s = 0.5 percent and 3 percent.

## Metrics
Per chart timeframe: trades, rejections by reason, net Rs per lot per trade, win rate, profit factor, total net Rs per lot, max drawdown, 95 percent bootstrap CI of mean net Rs per trade (2000 resamples, seed 7), also the same trades' index-level R for comparison. Delta-neutral breakdown not done.

## Pass / fail (fixed now)
EDGE-IN-THIS-WINDOW only if, for BOTH chart timeframes, n >= 30 trades AND the CI lower bound of mean net Rs per trade is above 0 at s = 1.5 percent. Else NO EDGE (or INSUFFICIENT_SAMPLE if n < 30). Even a pass is one 4-month window and would need forward paper validation (n >= 30 new trades) before any belief.

## Not claimed
No profitability. Last-traded bars are not executable quotes; strikes outside ATM+/-5 of each expiry are missing from the data, so some signals are untestable and that coverage loss is reported.
