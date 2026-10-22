# NIFTY tick-rule CVD proxy -- signal-existence screen

**Generated: 2026-09-28T19:40:09.208461+00:00**

## Rule tested

CVD (tick-rule signed volume, session-reset) crosses its own 20-bar SMA -> enter next-bar-open, 15.0pt stop / 30.0pt target, 15-bar max hold, on the NIFTY index level.

## Data sources (real, not fabricated)

- market_candles (DHAN_HISTORICAL), NIFTY INDEX, 1m, 2021-09-29..2026-09-28

## Disclosed interpretation choices

- Tick-rule proxy only -- NOT the live engine's quote-rule-primary classification (historical OHLCV has no bid/ask depth to run the quote rule at all)
- Simulated on the raw INDEX level, no real option/future execution cost applied
- CVD resets every session (no overnight carry)
- Fixed 15.0pt/30.0pt stop/target, 15-min max hold -- not optimized

## Data

NIFTY INDEX 1-min bars, real NSE trading hours only, 2021-09-29 to 2026-09-28 (463,656 bars). 44,431 raw CVD-cross signals found, 5,043 actually simulated (after no-look-ahead scan-index dedup).

## Report (gross points, no costs applied -- see honest-scope note above)

```json
{
  "n": 5043,
  "win_rate": 0.398,
  "profit_factor": 0.974,
  "avg_pnl": -0.207,
  "total_pnl": -1041.7,
  "verdict": "OK",
  "by_tag": {
    "LONG": {
      "n": 4638,
      "win_rate": 0.398,
      "avg_pnl": -0.24
    },
    "SHORT": {
      "n": 405,
      "win_rate": 0.402,
      "avg_pnl": 0.23
    }
  }
}
```

## Bottom line

n=5043, verdict=OK. profit_factor=0.974, win_rate=0.398, avg_pnl_points=-0.207. This is a tick-rule PROXY screen on the raw index level with no execution costs -- even a positive result here is NOT evidence the live quote-rule signal has edge, and NOT tradeable as-is without a real-cost, real-instrument re-run.
