# India-VIX "trap check" filter on Winners Scalper — FINDINGS 2026-10-25 (spec 664f504; scripts/winners_scalper_vixtrap.py; results_vixtrap.json)
Trap = BUY while VIX is above its value 3 chart bars ago, or SELL while VIX is not above it (Pine MODULE 2). Flagged entries skipped.

## Verdict: NO-GO (both tests, all three lookbacks)
| test | trades | flagged | mean ALL | mean KEPT | mean REMOVED | gain | kept CI lo | perm p |
|---|---|---|---|---|---|---|---|---|
| index X2/15m, 6 indices, 2022-26 (R) | 4,842 | 25 % | +0.028 | +0.016 | +0.065 | -0.012 | -0.038 | 0.95 |
| options NIFTY+SENSEX 2024-26 (Rs) | 918 | 22 % | +7.8 | +6.1 | +13.4 | -1.6 | -398 | 0.52 |
Lookback 1 and 5 bars: gain -0.002 and 0.000 R. The trades the check flags as "traps" are NOT worse than the rest - slightly better. Skipping the non-traps instead (inverse filter) does no better by construction of the same numbers. In 2022-26 the whole signal earns only +0.028 R at index level, so there was little to improve.
Conclusion: the VIX trap check does not separate winning from losing entries. It is not added to the smart chart (that would also need multi-symbol request.security in the sandboxed Pine runner, a security-sensitive change not justified by a null filter).
