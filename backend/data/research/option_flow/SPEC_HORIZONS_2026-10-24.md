# Option-flow extension, step 2: 15- and 60-minute horizons — pre-registered spec (2026-10-24, before any 15/60-minute number is computed)

## Why
At 30 minutes the replicated signal (NIFTY / SENSEX lift about +0.011 AUC, carried by F3) earns about +0.5 to +1.2 bp gross in the best 10 % of bars, break-even against a 1-1.5 bp cost. The cost is fixed while the average index move grows with the horizon (about 5 bp at 5 minutes, 11 at 30, roughly 18-20 at 60), so a longer horizon may clear the cost if the predictive edge does not fade. A shorter horizon (15 minutes) tests whether the effect is faster than 30 minutes. The five-sessions-per-expiry pull already covers the full life of every weekly contract, so no new data is needed.

## Design (everything else identical to SPEC_2026-10-24.md and SPEC_SENSEX_REPLICATION_2026-10-24.md)
Same data, features F1..F5 (15-minute lookbacks), price-only baseline B, models (logistic, gradient boosting), quarterly walk-forward, day-block bootstrap. Only the target changes: NIFTY / SENSEX direction and return over the next 15 minutes and over the next 60 minutes (same session; points with fewer than 60 minutes left in the session are dropped for the 60-minute target). Costs for the net gate: NIFTY 1.0 bp, SENSEX 1.5 bp (round trip).

## Labels per horizon (fixed now)
REPLICATES at horizon h if, on BOTH indices: lift over the price-only baseline >= +0.005 for BOTH models, univariate F3 AUC > 0.510, and full-model AUC > 0.50 in at least 5 test quarters.
STRONG at horizon h if, on BOTH indices, G1-G4 of the main spec pass (AUC >= 0.53 with CI lower > 0.50; lift >= 0.010 with paired CI lower > 0; top-decile net return after cost > 0 with CI lower > 0 and >= 300 signals; AUC > 0.50 in >= 5 quarters).
Family size: 2 horizons x 2 indices x 2 models; the Bonferroni level for the CI-based gates is 0.05 / 8 two-sided only for the STRONG label; REPLICATES uses point criteria as above.

## Consequences fixed in advance
STRONG at any horizon: a pre-registered live-validation plan on that horizon (the live feed already records F3; its forward-return column gets the extra horizon). Only REPLICATES: the live feed adds that horizon to its forward evaluation. Neither: the option-flow line is a real but non-tradable information effect and stays an observation-only feed. Never an order gate.
