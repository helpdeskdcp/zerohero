# Candle-AI — CPCV re-check (dated addendum to SPEC_2026-10-24.md; the original spec and its NO-GO verdict are untouched)
Written BEFORE the CPCV numbers are computed.

## Question
The walk-forward NO-GO used one chronological path per instrument. Does a Combinatorial Purged CV (many recombined paths, training allowed on both sides of the test block — i.e. *more* favourable to a model than walk-forward) show a robust net edge, or does it confirm NO-GO?

## Design
- skfolio.model_selection.CombinatorialPurgedCV, n_folds=10, n_test_folds=2 -> 45 splits, 9 recombined paths. purged_size=150 bars (2 sessions), embargo_size=75 bars (1 session). Same data (NIFTY, BANKNIFTY 5m 2015-2026), same features, same target, same model M2 (HistGB + isotonic on the last 20 % of the train indices) and baseline B1. No parameter changed.
- Signals: per split, test bars whose |p-0.5| exceeds the 90th percentile of |p-0.5| on that split's calibration slice. Directional next-bar return in bps, minus 1.0 bp round trip. Daily PnL of a path = sum over its signal bars (0 on days without signals).
- Deflated Sharpe Ratio (Bailey & Lopez de Prado) of each path's daily PnL, with N_trials = 10 (every model/feature variant tried in this study family, rounded up) and the variance of the SRs across the 9 paths.

## Pass criteria (ALL, on NIFTY AND BANKNIFTY)
A1 at least 8 of 9 paths have AUC > 0.50 and the median path AUC >= 0.52.
A2 EVERY path has net PnL > 0 per signal at 1 bp (all 9 paths), and the median path net bps per signal CI (day-block) excludes 0.
A3 the median path has DSR >= 0.95.
Else: CPCV CONFIRMS NO-GO. Passing would only move the engine to "worth a forward shadow log", never to a trade gate.
