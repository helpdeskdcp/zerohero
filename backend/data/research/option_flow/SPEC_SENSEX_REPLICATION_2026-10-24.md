# Option-flow replication on SENSEX — pre-registered spec (2026-10-24, written before any SENSEX option data is pulled)

## Why
The NIFTY study (SPEC_2026-10-24.md, FINDINGS_2026-10-24.md) was a NO-GO by its gates but the closest result of the project: AUC 0.518-0.520 vs a 0.508 price-only baseline (lift +0.010 / +0.012, CI touching zero), carried by the 15-minute change of the put-call-parity synthetic-future basis (F3). A near-miss on one index is not evidence. SENSEX (BSE) is the only other index with weekly options (107 weekly expiries 2024-10-04 .. 2026-10-22; BANKNIFTY / FINNIFTY / MIDCPNIFTY weeklies were discontinued, monthly only).

## Identical design, only the market constants change
Same five features F1..F5, same 15-minute lookbacks, same price-only baseline B, same 30-minute target, same quarterly walk-forward, same models (logistic, gradient boosting). Changes: index SENSEX (BSE_INDEX|SENSEX, 1-minute from market_candles INDEX), strike step 100, pulled band ATM +/-600 per session open, "near strikes" ATM +/-500, skew strikes ATM +/-200. Last 5 sessions of every weekly expiry from 2024-10-04 to 2026-10-22. Round-trip cost for the 30-minute edge gate: 1.5 bp (SENSEX futures are less liquid than NIFTY); 1.0 bp reported.

## Replication criterion (pre-registered; this is the primary question)
REPLICATES if ALL hold on the pooled walk-forward test set: (a) AUC lift over the price-only baseline >= +0.005 for BOTH the logistic and the gradient-boosting model; (b) univariate AUC of F3 > 0.510; (c) full-model AUC > 0.50 in at least 5 of the test quarters.
DOES NOT REPLICATE if the lift is < +0.005 for either model or F3 univariate AUC <= 0.505.
Anything in between: PARTIAL (reported, not acted on).
Additionally the original NIFTY gates G1-G4 are evaluated on SENSEX (net gate at 1.5 bp); passing all four = STRONG replication. A pooled NIFTY + SENSEX evaluation is reported descriptively only.

## Consequences fixed in advance
REPLICATES: the option-flow line is worth a NEW pre-registered extension (all sessions per expiry, bid/ask parity, other horizons) and a live observation-only feed. DOES NOT REPLICATE: the option-flow line is closed as noise. Nothing is ever an order gate on this evidence.

## Not claimed
Index-level, 30-minute, assumed costs, no option fills; SENSEX options are less liquid than NIFTY options (1-minute closes can be stale; stale bars older than 5 minutes are treated as missing).
