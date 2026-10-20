# ZeroHero claim test C1 — result (spec: CLAIM_TEST_SPEC_2026-10-20.md @3b5ab6a)

period ['2026-09-21', '2026-10-20'] · **C1 verdict: INCONCLUSIVE** · calibration claim: FAIL/NA

## PRIMARY: all closed trades
- n=25 days=9 · win rate 40.0% (Wilson95 23.4%..59.3%) · break-even win rate 0.493 · flat 0
- mean net/trade -136.25 (SE 213.66, t -0.64) · day-clustered one-sided p 0.763 · sum net -3406 (gross -3385, costs 22) · PF 0.69 · max DD 6731
## EXPLORATORY MCX
- n=25 days=9 · win rate 40.0% (Wilson95 23.4%..59.3%) · break-even win rate 0.493 · flat 0
- mean net/trade -136.25 (SE 213.66, t -0.64) · day-clustered one-sided p 0.763 · sum net -3406 (gross -3385, costs 22) · PF 0.69 · max DD 6731
## EXPLORATORY closed on/after 2026-09-21 (post calibration fix)
- n=25 days=9 · win rate 40.0% (Wilson95 23.4%..59.3%) · break-even win rate 0.493 · flat 0
- mean net/trade -136.25 (SE 213.66, t -0.64) · day-clustered one-sided p 0.763 · sum net -3406 (gross -3385, costs 22) · PF 0.69 · max DD 6731

---
## Post-hoc exploratory addendum (NOT the pre-registered test; cannot rescue or overturn the verdict above)
The pre-registered primary population is tiny because `net_pnl` is only populated from 2026-09-21 (n=25, INCONCLUSIVE). Looking at the larger ledger with the older `pnl` column (357 closed trades; 128 trades have cost_model_status UNCALIBRATED, 204 have none, so costs are NOT reliably netted):

| subset | n | days | win rate | break-even win rate | mean P&L/trade | sum | day-clustered one-sided p (mean>0) | PF |
|---|---|---|---|---|---|---|---|---|
| all closed | 357 | 36 | 44.3% | 59.0% | -77.9 | -27,821 | 0.986 | 0.55 |
| index options (NIFTY/BANKNIFTY/SENSEX) | 210 | 34 | 46.7% | 64.1% | -116.0 | -24,357 | 0.973 | 0.49 |
| MCX | 143 | 25 | 40.6% | 47.8% | -25.5 | -3,652 | 0.675 | 0.75 |
| strategy AUTOSCALP (autonomous engine) | 199 | 21 | 43.2% | 43.0% | +0.04 | +9 | 0.606 | 1.01 |
| strategy MANUAL (hand-entered paper rows) | 151 | 28 | 47.0% | 61.3% | -174.7 | -26,383 | 0.996 | 0.56 |

**Reading:** the autonomous engine is statistically indistinguishable from break-even (and costs are not reliably netted); MANUAL rows drive the loss. Nothing here supports "win probability higher than required". Calibration claim: not assessable on the pre-registered population (only 25 trades with probability+net_pnl) -> not passed.
