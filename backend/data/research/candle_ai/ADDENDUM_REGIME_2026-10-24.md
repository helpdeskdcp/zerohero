# Candle-AI — HMM / GARCH regime layer (dated addendum to SPEC_2026-10-24.md; original spec and NO-GO untouched)
Written BEFORE any regime result is computed.

## Hypothesis
The next-candle ranking signal (AUC ~0.53) is real but its gross edge (~0.6 bp per signal) is below the 1 bp round-trip cost. The cost is fixed in bps while the expected directional gain scales with volatility (E[gain] ~ (2p-1) * sigma_next * 0.80). So in HIGH-volatility regimes the same probability edge may clear the cost. Can a causal regime layer find those bars?

## Components (app/candle_ai/regime.py)
- GARCH(1,1), zero mean, normal innovations, on intraday 5m returns in bps (first bar of a session uses close/open, overnight gaps excluded). Parameters fitted on TRAIN years only, then applied with FIXED parameters over the whole series (the variance recursion uses the past only). Feature `sigma_next[t]` = conditional volatility of bar t+1 given information up to t. `sigma_ratio` = sigma_next / train median.
- Gaussian HMM, 3 states, full covariance, on [intraday return bps, log(range bps + 1)] standardised with train mean/std; fitted on train years only; states ordered by mean range (0 calm .. 2 turbulent). Decoding is the FORWARD (filtered) posterior only — never smoothed/Viterbi on the test span (a smoothed state would leak the future).
- The regime features (3 filtered state probabilities, sigma_next, sigma_ratio) are added to the M2 feature set.

## Walk-forward and rules (same as the main spec: test years 2018-2026, train = all earlier years, 1-day embargo, P1 = 2018-21, P2 = 2022-26, NIFTY and BANKNIFTY 5m)
- R0 (reference): M2 with regime features, plain top-10% confidence rule.
- R1: trade only if sigma_next >= the train 67th percentile AND |p-0.5| >= the 90th percentile of |p-0.5| among train-calibration rows with sigma_next >= that cut.
- R2: expected-edge rule: E = 2*|p-0.5|*sigma_next*0.7979 (bps); trade if E >= 1.5 bp.
Only R1 and R2 are candidate rules (N_trials for any deflation = 12 including earlier variants). No threshold is tuned on a test year.

## Gates for "regime layer worth a forward shadow log" (ALL, both instruments, both P1 and P2, for R1 OR R2 (same rule in all four cells))
G1 net edge: mean directional next-bar return - 1.0 bp > 0 with day-block bootstrap 95 % CI lower bound > 0, with >= 500 signals in the cell.
G2 the regime features do not hurt: M2+regime AUC >= M2 AUC - 0.002 in each cell.
G3 predicted vs realised: for R2, realised gross bps of the selected signals >= 0.5 * mean predicted E (the vol-scaling story must show up in the data), Spearman correlation between E and realised directional return > 0 on the test years.
Else: REGIME LAYER NO-GO. Passing would only license an observation-only shadow log, never a trade gate.

## Not claimed
Index-level edge only: no option premium, theta, spread, fills.
