# Index-level vs CE/PE results — Hybrid MP v13 fixed (NIFTY 5m)

Index (from `BACKTEST_CORRECTNESS_REPORT.md`): gross +0.027/+0.033/+0.036 R, net(2 bps) −0.128/−0.142/−0.145 R (TRAIN/VALID/OOS).

## Index signals vs executable CE/PE (`option_evaluation.txt`)
```
index trades (fixed, 5m): 6185 | assumptions: lot=75, spread/side=Rs0.1, slippage/side=Rs0.05, charges approx (brokerage Rs20.0/order, STT 0.1% sell, exch 0.0503%)

PART A - MODEL (constant IV; BS repricing at the exit-bar close; partial exits priced at the final exit time)

IV 12%:
  no spread/slippage/charges   n= 3126 mean net Rs/lot    +184.2 ±  64.4 | mean % of premium +11.45% | win 48.1% | avg premium   80.5 | index gross R +0.034
  with spread, slippage, charges n= 3126 mean net Rs/lot    +100.8 ±  64.3 | mean % of premium +10.53% | win 45.7% | avg premium   80.7 | index gross R +0.034

IV 14%:
  no spread/slippage/charges   n= 3129 mean net Rs/lot    +142.5 ±  63.2 | mean % of premium  +7.53% | win 47.7% | avg premium   93.7 | index gross R +0.034
  with spread, slippage, charges n= 3129 mean net Rs/lot     +57.0 ±  63.1 | mean % of premium  +6.84% | win 44.5% | avg premium   93.8 | index gross R +0.034
  VALID 2022-2023              n= 1363 mean net Rs/lot     +31.7 ±  79.5 | mean % of premium  +6.37% | win 44.2% | avg premium   76.5 | index gross R +0.032
  OOS 2024-                    n= 1766 mean net Rs/lot     +76.5 ±  93.4 | mean % of premium  +7.21% | win 44.7% | avg premium  107.2 | index gross R +0.036
  DTE 0 (expiry day)           n=  599 mean net Rs/lot    +304.9 ± 170.0 | mean % of premium +31.75% | win 37.7% | avg premium   23.4 | index gross R +0.100
  DTE 1                        n=  638 mean net Rs/lot      -7.5 ± 122.9 | mean % of premium  +1.57% | win 45.8% | avg premium   67.3 | index gross R +0.001
  DTE 2                        n=  519 mean net Rs/lot     -17.1 ± 164.5 | mean % of premium  +0.67% | win 44.1% | avg premium   88.9 | index gross R -0.021
  DTE 3-4                      n=  607 mean net Rs/lot     -10.4 ± 124.5 | mean % of premium  +0.89% | win 46.5% | avg premium  117.4 | index gross R +0.035
  DTE 5+                       n=  766 mean net Rs/lot     +20.3 ± 125.8 | mean % of premium  +0.65% | win 47.4% | avg premium  155.7 | index gross R +0.048
  capital Rs12,000 / 1% risk: 1 lot is within the risk budget and capital in 0.5% of trades; reasons {'ONE_LOT_EXCEEDS_RISK_BUDGET': 2743, 'OK': 17, 'ONE_LOT_EXCEEDS_CAPITAL': 369}

IV 16%:
  no spread/slippage/charges   n= 3131 mean net Rs/lot    +105.6 ±  62.1 | mean % of premium  +5.56% | win 47.0% | avg premium  106.9 | index gross R +0.035
  with spread, slippage, charges n= 3131 mean net Rs/lot     +17.9 ±  62.0 | mean % of premium  +4.92% | win 43.4% | avg premium  107.0 | index gross R +0.035

IV 20%:
  no spread/slippage/charges   n= 3131 mean net Rs/lot     +39.0 ±  60.4 | mean % of premium  +2.10% | win 45.6% | avg premium  133.4 | index gross R +0.035
  with spread, slippage, charges n= 3131 mean net Rs/lot     -53.0 ±  60.3 | mean % of premium  +1.66% | win 41.9% | avg premium  133.6 | index gross R +0.035

PART B - REAL option bars (Dhan rolling, 1m), signals 2026-08-03..2026-08-17
  index signals in window: 24; priced from real bars: 13; no data for the contract/minute: 11
  REAL bars, net of costs      n=   13 mean net Rs/lot    -771.0 ± 595.4 | mean % of premium -14.02% | win 15.4% | avg premium   90.3 | index gross R -0.111
  -> INSUFFICIENT SAMPLE (11 sessions): shown only to prove the pipeline prices real contracts; no conclusion may be drawn.
```
Reading: (1) the index-level result (gross +0.034 R) and the option result are **different measurements**; the MODEL option P&L changes sign with the assumed IV alone (+₹101 / +₹57 / +₹18 / −₹53 per lot at 12/14/16/20%), all intervals include 0, and the expiry-day bucket (+31%) is the usual cheap-premium convexity artefact (constant IV, ₹0.15 spread) — it is not evidence of profit. (2) The only hard finding is executability: at ₹12,000 and 1% risk **0.5% of signals** can be sized (87.7% need more than the ₹120 risk budget for a single lot; 11.8% cannot afford one lot). (3) Real option bars exist for 11 sessions only: 13 of 24 signals priced, mean −₹771 ± 595 per lot — **INSUFFICIENT SAMPLE**, shown to prove the pipeline.
