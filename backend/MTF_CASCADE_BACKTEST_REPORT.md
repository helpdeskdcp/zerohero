# MTF Cascade -- Real-Data Backtest Report

Real Kaggle NIFTY 5m data (2015-2026). Monthly bias uses a genuine, anti-repaint CONFIRMED 20-month SMA (never the developing/current month). Every ENTERED signal is graded by walking REAL forward bars through its own staged 1R->2R->3R->4R trailing-stop logic -- the exact mechanism the live engine would use, not a proxy.

- Total decision points: 200
- Entries (BUY_CE/BUY_PE): 9 (4.50%)
- Of which ZTH sideways-gate signals: 0

## TRAIN (68 decision points, 3 resolved entries)

- Win rate (R > 0): 66.67%
- Average R achieved: 0.333
- Exit reasons: {'STOPPED_OUT': 3}

## VALIDATION (67 decision points, 5 resolved entries)

- Win rate (R > 0): 0.00%
- Average R achieved: -0.800
- Exit reasons: {'STOPPED_OUT': 5}

## OOS (65 decision points, 1 resolved entries)

- Win rate (R > 0): 100.00%
- Average R achieved: 4.000
- Exit reasons: {'FINAL_TARGET': 1}

## Caveats

- **Sample size is far too small to draw any performance conclusion**: only 9 resolved entries total across all three periods. This backtest proves the PIPELINE runs correctly end-to-end on real data (anti-repaint Monthly/Weekly SMA, the full cascade, prev-day-level checks, staged 1R-4R trailing) -- it is NOT evidence of real directional edge either way. Treat the win-rate/avg-R numbers per period as a mechanism check, not a result.
- One decision check per real TRADING DAY (DECIDE_EVERY_SEC=86400) to keep this tractable given the Monthly-SMA lookback requirement (a full resample over tens of thousands of bars per call makes intraday cadence intractable in pure Python at this dataset scale) -- a live system would decide far more often intraday; this backtest validates the mechanism, not realistic signal frequency.
- No threshold or weight in MTFConfig was tuned on any of these periods.
- Momentum-gating for R3/R4 trailing uses a lightweight RSI check recomputed at each forward bar, not the full 7-timeframe cascade re-run at every step (that would be prohibitively expensive for a backtest of this scale) -- documented simplification.

Raw per-row dump: `data/research/mtf_cascade/mtf_backtest_rows.json`