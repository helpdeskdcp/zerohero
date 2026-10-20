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

---
## CORRECTION + exploratory v2 (post-hoc; supersedes the rupee sums in the previous addendum)
**Correction:** `ai_paper_trades.pnl` mixes units (premium POINTS when quantity=1, RUPEES otherwise), so the earlier addendum's `sum` / `mean P&L` columns were apples-to-oranges. Win rates and the AUTOSCALP-vs-MANUAL ordering do not depend on the unit, but the rupee magnitudes (e.g. MANUAL -26,383) must not be quoted.
Unit-free re-analysis: return = (exit-entry)/entry of the option premium, minus MODELLED costs (index options: formula estimate, 0.75 pt/leg slippage; MCX NG/CRUDE: validated per-lot profile + 0.5 pt). Instruments without a cost model are skipped: {'NATGASMINI': 12, 'ALOKINDS': 2, 'COALINDIA': 1, 'RPOWER': 1, 'CRUDEOILM': 3}

| subset | n | days | win rate (Wilson95) | break-even WR | mean gross % | mean cost % | mean NET % | day-clustered p (mean>0) | PF |
|---|---|---|---|---|---|---|---|---|---|
| ALL (modelled-cost net, unit-free) | 338 | 35 | 28.4% (23.9%-33.4%) | 0.679 | -2.55 | 4.33 | -6.88 | 1.000 | 0.19 |
| strategy=AUTOSCALP | 199 | 21 | 25.6% (20.1%-32.1%) | 0.523 | -0.34 | 2.48 | -2.82 | 0.900 | 0.31 |
| strategy=MANUAL | 133 | 26 | 33.8% (26.3%-42.2%) | 0.780 | -5.83 | 6.61 | -12.44 | 1.000 | 0.14 |
| family=INDEX_OPTIONS | 210 | 34 | 35.7% (29.5%-42.4%) | 0.710 | -3.53 | 3.32 | -6.85 | 1.000 | 0.23 |
| family=MCX_NG_CRUDE | 128 | 22 | 16.4% (11.0%-23.8%) | 0.631 | -0.95 | 5.99 | -6.93 | 0.984 | 0.11 |
| AUTOSCALP x INDEX_OPTIONS | 101 | 20 | 34.7% (26.1%-44.3%) | 0.448 | 0.27 | 1.40 | -1.12 | 0.777 | 0.65 |
| AUTOSCALP x MCX_NG_CRUDE | 98 | 12 | 16.3% (10.3%-24.9%) | 0.689 | -0.96 | 3.60 | -4.57 | 1.000 | 0.09 |

Still exploratory: ledger rows are the same data the rules were tuned on; modelled (not contract-note) costs; no independent holdout. It cannot support a 'proven' claim.
**Slippage sensitivity (AUTOSCALP only, n=199, unit-free):** mean gross return -0.34%/trade (no edge before costs). Net with zero slippage (statutory+brokerage only) -1.26%; with 0.25 pt/leg -2.00%; with 0.75 (index) / 0.5 (MCX) -2.82%. So the negative net result does not hinge on the slippage assumption: gross itself is ~0 and statutory costs alone make it negative.
