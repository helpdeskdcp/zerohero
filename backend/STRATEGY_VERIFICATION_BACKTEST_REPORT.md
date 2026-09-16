# Strategy Verification Engine -- Backtest Report

RAW signal = existing, unmodified `state_classifier.classify()` (fed by `sr_engine.compute_sr()`), read-only, unchanged. VERIFIED signal = the same raw signal passed through `StrategyVerifier` -- only counted when the state machine reaches ENTRY_READY. Both graded with the same ATR-normalized forward-index-move proxy (12-bar horizon, 1x ATR) -- this measures DIRECTIONAL signal quality, not option-premium P&L (a generic BUY/SELL raw signal has no contract to price).

- Data: real NIFTY 5m index bars, `/root/oi_dashboard/oi_history.db`, 2026-07-13..2026-08-28
- Total decision rows: 1568
- Total raw signals (BUY or SELL): 1568
- Verified signals (CE or PE scored, any state): 796
- CE signals: 366
- PE signals: 430
- NO_TRADE: 772
- Signal conflicts: 120
- Entry-ready (final verified trades): 451

## TRAIN (455 decision rows)

| Metric | BEFORE (raw signal) | AFTER (verified signal) |
|---|---|---|
| Graded signals | 445 | 125 |
| Win rate | 0.5303 | 0.528 |
| Profit factor | 1.129 | 1.119 |
| Expectancy (R) | 0.0607 | 0.056 |
| Avg favorable excursion | 34.9209 | 30.0376 |
| Avg adverse excursion | 37.46 | 37.986 |
| Max drawdown (R) | -27.0 | -14.0 |
| CE accuracy | 0.5108 | 0.4222 |
| PE accuracy | 0.5514 | 0.5875 |
| Signal volume reduction | -- | 0.7191 |

## VALIDATION (573 decision rows)

| Metric | BEFORE (raw signal) | AFTER (verified signal) |
|---|---|---|
| Graded signals | 554 | 153 |
| Win rate | 0.435 | 0.451 |
| Profit factor | 0.77 | 0.821 |
| Expectancy (R) | -0.13 | -0.098 |
| Avg favorable excursion | 27.7478 | 25.4663 |
| Avg adverse excursion | 35.2403 | 34.9673 |
| Max drawdown (R) | -72.0 | -16.0 |
| CE accuracy | 0.4203 | 0.3846 |
| PE accuracy | 0.4517 | 0.52 |
| Signal volume reduction | -- | 0.7238 |

## OOS (540 decision rows)

| Metric | BEFORE (raw signal) | AFTER (verified signal) |
|---|---|---|
| Graded signals | 527 | 153 |
| Win rate | 0.4535 | 0.4575 |
| Profit factor | 0.83 | 0.843 |
| Expectancy (R) | -0.093 | -0.085 |
| Avg favorable excursion | 23.7498 | 25.6092 |
| Avg adverse excursion | 23.8398 | 20.85 |
| Max drawdown (R) | -49.0 | -25.0 |
| CE accuracy | 0.4198 | 0.3585 |
| PE accuracy | 0.4824 | 0.51 |
| Signal volume reduction | -- | 0.7097 |

## Caveats

- Outcome grading is an ATR-normalized directional proxy, not real option P&L -- profit factor/expectancy/drawdown here are in R-multiples (1R = the ATR threshold), not points or premium.
- No threshold or weight was tuned on any of these three periods before this run -- the same default StrategyConfig() was used throughout, so TRAIN/VALIDATION/OOS consistency (or the lack of it) is genuine, not a leakage artifact.
- "Win rate" for the raw signal counts EVERY BUY/SELL read; the verified signal's win rate only counts rows that reached ENTRY_READY -- the entire point of comparing them is whether the verification layer trades less often but more accurately.

Raw per-row dump: `data/research/strategy_verification/backtest_rows.json`