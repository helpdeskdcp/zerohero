# ORDERFLOW_ENGINE v1 -- NIFTY -- backtest

_generated 2026-09-09T03:52:12.917163+00:00 · tf 5m · 2016-01-01..2025-12-31 · runtime 29.5s_

## Data capability (spec §20)
```
{
 "symbol": "NIFTY",
 "source": "kaggle",
 "tf_min": 5,
 "n_bars": 198534,
 "n_sessions": 2599,
 "range": [
  "2016-01-01",
  "2025-12-24"
 ],
 "has_trades": false,
 "has_aggressor": false,
 "has_L1": false,
 "has_L2_snapshots": false,
 "has_L2_stream": false,
 "volume_available": false,
 "cadence_sec": 300,
 "true_orderflow": false,
 "note": "TRUE ORDER FLOW NOT AVAILABLE -- NIFTY cash index: no aggressor side, no per-trade size, no tick feed, volume=0. delta / CVD / footprint / imbalance / absorption are PROXY or OMITTED."
}
```

## VERDICT: **NO-GO**

Do NOT wire to live. The two-stage order-flow-proxy + optimal-entry engine does not clear OOS net / PF / multi-year with the calibrated zone. Consistent with the spec's data-limitation verdict for a cash-index proxy engine.

- chosen retracement zone z* = **0.3** (_NO zone is positive on both TRAIN and VALIDATION -> engine is NOT VALIDATED; z* is the least-bad TRAIN zone, reported for record only_)
- splits: TRAIN 2016-01-01..2021-06-25 (1429) · VAL 520 · OOS 650 sessions
- signals 3044 · entries 294 · status {"EXPIRED_TIMEOUT": 1125, "EXPIRED_NOCHASE": 1420, "ENTRY_READY": 294, "EXPIRED_INVALIDATED": 205}

## Zone calibration (TRAIN)

| z | fill% | n | win% | expR | PF | net pt | MAE_R | MFE_R | SL% | T1% | T2% | T3% |
|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 0.0 | 0.0 | 0 | | | | | | | | | | |
| 0.1 | 0.007 | 12 | 41.7 | 0.3488 | 1.545 | 73.8 | -0.88 | 1.35 | 50.0 | 33.3 | 8.3 | 0.0 |
| 0.2 | 0.062 | 100 | 43.0 | 0.1389 | 1.262 | 298.9 | -0.908 | 1.303 | 45.0 | 34.0 | 10.0 | 0.0 |
| 0.3 | 0.106 | 170 | 46.5 | 0.1638 | 1.177 | 362.9 | -0.901 | 1.338 | 41.8 | 36.5 | 10.0 | 0.0 |
| 0.4 | 0.132 | 211 | 45.0 | 0.1563 | 1.472 | 968.7 | -0.914 | 1.297 | 44.1 | 33.7 | 11.8 | 0.5 |
| 0.5 | 0.112 | 179 | 41.3 | 0.0314 | 1.184 | 336.4 | -0.939 | 1.161 | 43.0 | 27.9 | 12.3 | 1.1 |
| 0.6 | 0.077 | 124 | 42.7 | -0.0444 | 1.001 | 1.4 | -0.955 | 1.096 | 44.4 | 24.2 | 9.7 | 0.0 |

## Zone calibration (VALIDATION)

| z | n | win% | expR | PF | net pt |
|--:|--:|--:|--:|--:|--:|
| 0.1 | 6 | 50.0 | 0.6454 | 1.566 | 59.0 |
| 0.2 | 40 | 42.5 | 0.0326 | 1.006 | 4.0 |
| 0.3 | 62 | 38.7 | -0.0976 | 0.728 | -314.4 |
| 0.4 | 47 | 34.0 | -0.1159 | 0.728 | -234.7 |
| 0.5 | 43 | 32.6 | -0.1422 | 0.673 | -269.2 |
| 0.6 | 30 | 40.0 | 0.1636 | 0.773 | -124.1 |

## Performance with z* (spec: two-stage, entry-quality gated)

| split | n | W | L | win% | expR | PF | net pt | maxDD | SL% | T1% | T3% | avgEQ | avgWait |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| all | 294 | 126 | 168 | 42.9 | 0.0546 | 0.955 | -204.0 | -1099.6 | 45.2 | 33.0 | 0.0 | 68.6 | 6.61 |
| train | 170 | 79 | 91 | 46.5 | 0.1638 | 1.177 | 362.9 | -468.8 | 41.8 | 36.5 | 0.0 | 68.3 | 6.44 |
| val | 62 | 24 | 38 | 38.7 | -0.0976 | 0.728 | -314.4 | -521.0 | 46.8 | 27.4 | 0.0 | 68.1 | 6.03 |
| oos | 62 | 23 | 39 | 37.1 | -0.0925 | 0.811 | -252.5 | -665.5 | 53.2 | 29.0 | 0.0 | 69.9 | 7.68 |

## By year (net points)

| year | n | win% | expR | PF | net pt |
|--:|--:|--:|--:|--:|--:|
| 2016 | 38 | 52.6 | 0.3139 | 1.553 | 156.9 |
| 2017 | 23 | 65.2 | 0.5938 | 2.816 | 182.8 |
| 2018 | 35 | 40.0 | -0.0008 | 0.808 | -71.2 |
| 2019 | 30 | 36.7 | -0.0892 | 0.763 | -110.5 |
| 2020 | 22 | 36.4 | -0.1439 | 0.677 | -150.1 |
| 2021 | 37 | 46.0 | 0.2056 | 1.52 | 310.7 |
| 2022 | 26 | 38.5 | 0.0398 | 0.834 | -92.8 |
| 2023 | 38 | 28.9 | -0.3692 | 0.47 | -363.2 |
| 2024 | 20 | 35.0 | -0.1631 | 0.634 | -196.7 |
| 2025 | 25 | 52.0 | 0.1993 | 1.275 | 130.0 |

## Walk-forward (expanding folds, z* fixed)

| fold | test | n | win% | expR | PF | net pt |
|--|--|--:|--:|--:|--:|--:|
| 1 | 2017-06-05..2018-11-06 | 44 | 47.7 | 0.1568 | 1.1 | 36.7 |
| 2 | 2018-11-07..2020-04-07 | 36 | 33.3 | -0.1633 | 0.575 | -291.1 |
| 3 | 2020-04-08..2021-09-10 | 47 | 44.7 | 0.1961 | 1.566 | 400.5 |
| 4 | 2021-09-13..2023-02-13 | 43 | 39.5 | -0.0849 | 0.752 | -217.8 |
| 5 | 2023-02-14..2024-07-19 | 42 | 28.6 | -0.2839 | 0.537 | -427.0 |
| 6 | 2024-07-22..2025-12-22 | 34 | 47.1 | 0.0449 | 1.064 | 41.4 |

_Learning dataset (every signal + path) written alongside as `orderflow_v1_learning_*.jsonl` for continuous zone re-calibration._