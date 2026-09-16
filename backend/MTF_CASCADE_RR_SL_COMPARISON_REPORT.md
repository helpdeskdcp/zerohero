# MTF Cascade -- 1:2 vs 1:3 Target, With vs Without Stop-Loss

Real Kaggle NIFTY 5m data. All four variants are simulated on the SAME real entries (identical timestamps/direction/entry/SL from one discovery pass) and the SAME forward real bars -- only the target R-multiple and whether the stop-loss is honored differ between variants, so this is a controlled, apples-to-apples comparison.

- Total real entries found: 9
- **This is a very small sample (9 entries) -- treat every number below as illustrative of the MECHANISM, not a reliable performance estimate. See the mechanism-only caveat in MTF_CASCADE_BACKTEST_REPORT.md for why the discovery cadence is this sparse.**

## Comparison

| Variant | n | Win rate | Avg R | Total R | Profit factor | Max DD (R) | Exit reasons |
|---|---|---|---|---|---|---|---|
| 1:2_with_SL | 9 | 33.33% | 0.0 | 0.0 | 1.0 | -6.0 | {'TARGET': 3, 'STOPPED_OUT': 6} |
| 1:2_without_SL | 9 | 44.44% | -0.6576 | -5.918 | 0.575 | -12.872 | {'TARGET': 4, 'TIME_CUTOFF': 5} |
| 1:3_with_SL | 9 | 11.11% | -0.5556 | -5.0 | 0.375 | -8.0 | {'TARGET': 1, 'STOPPED_OUT': 8} |
| 1:3_without_SL | 9 | 33.33% | -0.6844 | -6.16 | 0.594 | -12.872 | {'TARGET': 3, 'TIME_CUTOFF': 6} |

## What this isolates

- **1:2 vs 1:3**: a smaller target hits more often but caps the payoff per winner; a larger target hits less often but pays more when it does. Compare win_rate and avg_r between the with-SL rows at each target to see which trade-off actually paid off on these specific entries.
- **With SL vs without SL**: removing the stop only changes the outcome for trades that would have been stopped out -- those either recover to the target (SL was 'wrong', without-SL wins), keep going against the position (without-SL bleeds further until the time cutoff, usually worse), or churn sideways (little difference). The 'without SL' avg-R and max-drawdown rows show which of those actually happened here.

## Caveats

- "Without stop-loss" still exits at a hard time cutoff (300 bars) if neither the target nor an early reversal closes the trade first -- marked to the real last close, never left open indefinitely or treated as zero risk.
- Same-bar target+SL ambiguity (both levels touched within one real bar) resolves conservatively to STOPPED_OUT, the same convention used throughout this codebase's other backtests.
- No threshold, weight, or entry criterion was changed from the approved MTF cascade to produce these entries -- only the EXIT rule varies between the four variants.

Raw per-entry, per-variant dump: `data/research/mtf_cascade/rr_sl_comparison_rows.json`