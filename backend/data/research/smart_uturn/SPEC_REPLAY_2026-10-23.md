# Smart U-Turn v2.1 (Python port) — historical replay spec, pre-registered 2026-10-23

Written before the Upstox replay and before the Kaggle P1/P2 replay. DISCLOSURE: a single smoke run (Kaggle NIFTY 2024, 5-minute chart, defaults) was executed while building the tool and its headline was seen (440 trades, net about -0.17 R per trade). 2024 is therefore not an independent sample for the 5m result; P2 below contains it.

## Question
Does the faithful Python port of the Pine signals, replayed on the INDEX with conservative execution, show positive net expectancy? (Index-level only: this is not an option-premium result; the owner's own trades are options.)

## Data
(a) Upstox NIFTY index 1-minute candles from the 4-month pull the owner asked for (about 2026-06 .. 2026-10), deleted after the run as instructed.
(b) Extra, already on disk: Kaggle NIFTY 1-minute 2015-2025 (volume is zero: the volume filter stays off, as in the Pine default). P1 = 2015-2019, P2 = 2020-2025. Reported per period.

## Configuration (fixed now; Pine defaults for signals)
SignalParams defaults (9/21/14/8/3/1.3/strict/1 vote/volume off/cooldown 3/ATR 14/0.2/0.5/1R-2R-3R), mintick 0.05. BacktestParams: NEXT_OPEN entry, THIRDS exit, IGNORE_WHILE_OPEN, STOP_FIRST, flat at session end, cost 2.0 index points per round trip, 1% risk of 500,000 per trade, no compounding.
Chart timeframes reported: 5 and 15 (both, no selection). Descriptive sensitivities (cannot change the verdict): cost 0 and 4 points; entry SIGNAL_CLOSE (Pine's own optimistic entry); exit models T1, T2, T3.

## Pass / fail
EDGE only if, for BOTH chart timeframes and BOTH of P1 and P2: n >= 30 trades and the 95% bootstrap interval of the net expectancy (in R) has a lower bound above 0. Anything else is NO EDGE (or INSUFFICIENT_SAMPLE if n < 30). The Upstox 4-month result is reported alone as INSUFFICIENT_SAMPLE if n < 30. No parameter is tuned after seeing results.

## Not claimed
No profitability. A pass would still need an option-premium replay (spread, theta, costs) before any use.
