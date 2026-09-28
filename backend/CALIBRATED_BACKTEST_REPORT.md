# Calibrated Re-Backtest -- Real Cost Model + Shared Harness

**Generated: 2026-09-28T03:36:50.442483+00:00**

## Rule tested

Re-runs the Reversal Sweep strategy's real-option-premium test (DAILY+WEEKLY levels only -- 4H already proven net-negative and excluded) using the new shared_harness library end-to-end, and the new estimate_index_option_cost() real cost formula (published NSE/Angel One regulatory rates: brokerage, exchange txn, STT post-Budget-2026-27, stamp duty, SEBI fee, GST, disclosed 0.75pt slippage) in place of the original scripts' rough hand-rolled cost guess.

## Data sources (real, not fabricated)

- Real captured NIFTY index+option ticks, market_history.db::quote_snapshots, ~2026-09-02 to 09-25 (the entire real captured window that exists)
- Same hard sample-size ceiling as the original studies -- this is a cost-model recalculation on the SAME real trades, not a bigger sample

## Disclosed interpretation choices

- CPR+ORB not re-run against real premium -- already NO-GO at the index-points level before reaching a real premium test; nothing to recompute
- VWAP+EMA's real premium sample (n=3) is too thin for the cost-model choice to matter either way -- not re-run in detail here, see original VERDICT.md
- exit_premium looked up at the same real-tick-nearest-timestamp method as the original study (30 min tolerance) -- a signal with no real quote within tolerance is skipped, never fabricated

## GROSS P&L (no cost)

```json
{
  "n": 16,
  "win_rate": 0.5,
  "profit_factor": 0.567,
  "avg_pnl": -398.938,
  "total_pnl": -6383.0,
  "verdict": "INSUFFICIENT_SAMPLE",
  "by_tag": {
    "DAILY": {
      "n": 12,
      "win_rate": 0.5,
      "avg_pnl": -83.69
    },
    "WEEKLY": {
      "n": 4,
      "win_rate": 0.5,
      "avg_pnl": -1344.69
    }
  }
}
```

## NET P&L -- NEW real cost formula (FORMULA_ESTIMATE)

```json
{
  "n": 16,
  "win_rate": 0.4375,
  "profit_factor": 0.478,
  "avg_pnl": -510.376,
  "total_pnl": -8166.0,
  "verdict": "INSUFFICIENT_SAMPLE",
  "by_tag": {
    "DAILY": {
      "n": 12,
      "win_rate": 0.417,
      "avg_pnl": -193.26
    },
    "WEEKLY": {
      "n": 4,
      "win_rate": 0.5,
      "avg_pnl": -1461.72
    }
  }
}
```

## NET P&L -- OLD hand-rolled estimate (comparison only)

```json
{
  "n": 16,
  "win_rate": 0.5,
  "profit_factor": 0.514,
  "avg_pnl": -463.55,
  "total_pnl": -7416.8,
  "verdict": "INSUFFICIENT_SAMPLE",
  "by_tag": {
    "DAILY": {
      "n": 12,
      "win_rate": 0.5,
      "avg_pnl": -147.72
    },
    "WEEKLY": {
      "n": 4,
      "win_rate": 0.5,
      "avg_pnl": -1411.05
    }
  }
}
```

## Cost totals across all real trades

```json
{
  "old_estimate_total_cost": 1033.8,
  "new_formula_total_cost": 1783.01,
  "difference": 749.21
}
```

## Bottom line

n=16 real trades. The new, real regulatory-rate-based cost model changes the net P&L number but not the fundamental conclusion from the original study: n is still far too small (well under the 30-trade INSUFFICIENT_SAMPLE threshold) for any real GO/NO-GO call on real option premium. The value of this re-run is precision (a real cost formula instead of a guess), not a bigger or more confident sample -- that still requires more real captured trading days, which this system continues to accumulate.
