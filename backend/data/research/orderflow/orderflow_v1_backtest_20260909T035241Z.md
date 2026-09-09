# ORDERFLOW_ENGINE v1 -- NIFTY -- backtest

_generated 2026-09-09T03:52:35.444466+00:00 · tf 15m · 2016-01-01..2025-12-31 · runtime 8.0s_

## Data capability (spec §20)
```
{
 "symbol": "NIFTY",
 "source": "kaggle",
 "tf_min": 15,
 "n_bars": 68690,
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
 "cadence_sec": 900,
 "true_orderflow": false,
 "note": "TRUE ORDER FLOW NOT AVAILABLE -- NIFTY cash index: no aggressor side, no per-trade size, no tick feed, volume=0. delta / CVD / footprint / imbalance / absorption are PROXY or OMITTED."
}
```

## VERDICT: **NO-GO**

Do NOT wire to live. The two-stage order-flow-proxy + optimal-entry engine does not clear OOS net / PF / multi-year with the calibrated zone. Consistent with the spec's data-limitation verdict for a cash-index proxy engine.

- chosen retracement zone z* = **0.6** (_best TRAIN expectancy among n>=30 zones that is also VALIDATION-positive (train expR 0.0442, val expR 0.2766)_)
- splits: TRAIN 2016-01-01..2021-06-25 (1429) · VAL 520 · OOS 650 sessions
- signals 1301 · entries 72 · status {"EXPIRED_NOCHASE": 370, "EXPIRED_INVALIDATED": 109, "EXPIRED_TIMEOUT": 750, "ENTRY_READY": 72}

## Zone calibration (TRAIN)

| z | fill% | n | win% | expR | PF | net pt | MAE_R | MFE_R | SL% | T1% | T2% | T3% |
|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 0.0 | 0.0 | 0 | | | | | | | | | | |
| 0.1 | 0.01 | 7 | 71.4 | 0.4772 | 2.831 | 100.6 | -0.871 | 1.159 | 28.6 | 28.6 | 0.0 | 0.0 |
| 0.2 | 0.057 | 39 | 25.6 | -0.3097 | 0.351 | -544.0 | -1.047 | 0.713 | 46.2 | 10.3 | 0.0 | 0.0 |
| 0.3 | 0.067 | 46 | 19.6 | -0.559 | 0.155 | -1038.9 | -1.005 | 0.459 | 63.0 | 2.2 | 0.0 | 0.0 |
| 0.4 | 0.07 | 48 | 27.1 | -0.3208 | 0.335 | -693.8 | -0.921 | 0.642 | 52.1 | 10.4 | 4.2 | 0.0 |
| 0.5 | 0.066 | 45 | 44.4 | -0.0031 | 0.834 | -111.5 | -0.888 | 0.841 | 40.0 | 20.0 | 11.1 | 0.0 |
| 0.6 | 0.058 | 40 | 45.0 | 0.0442 | 0.936 | -34.6 | -0.898 | 0.96 | 40.0 | 20.0 | 10.0 | 0.0 |

## Zone calibration (VALIDATION)

| z | n | win% | expR | PF | net pt |
|--:|--:|--:|--:|--:|--:|
| 0.1 | 7 | 57.1 | 0.2492 | 1.27 | 40.5 |
| 0.2 | 16 | 43.8 | -0.0476 | 0.843 | -69.1 |
| 0.3 | 16 | 56.2 | 0.2223 | 1.317 | 112.4 |
| 0.4 | 22 | 68.2 | 0.5028 | 2.553 | 497.5 |
| 0.5 | 21 | 61.9 | 0.3894 | 2.188 | 357.1 |
| 0.6 | 16 | 56.2 | 0.2766 | 2.257 | 248.4 |

## Performance with z* (spec: two-stage, entry-quality gated)

| split | n | W | L | win% | expR | PF | net pt | maxDD | SL% | T1% | T3% | avgEQ | avgWait |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| all | 72 | 34 | 38 | 47.2 | 0.0822 | 1.095 | 105.9 | -315.4 | 31.9 | 18.1 | 0.0 | 69.6 | 6.31 |
| train | 40 | 18 | 22 | 45.0 | 0.0442 | 0.936 | -34.6 | -178.9 | 40.0 | 20.0 | 0.0 | 69.5 | 7.0 |
| val | 16 | 9 | 7 | 56.2 | 0.2766 | 2.257 | 248.4 | -76.4 | 25.0 | 18.8 | 0.0 | 72.8 | 4.81 |
| oos | 16 | 7 | 9 | 43.8 | -0.017 | 0.713 | -107.9 | -291.8 | 18.8 | 12.5 | 0.0 | 66.9 | 6.06 |

## By year (net points)

| year | n | win% | expR | PF | net pt |
|--:|--:|--:|--:|--:|--:|
| 2016 | 6 | 33.3 | -0.0267 | 0.715 | -16.1 |
| 2017 | 6 | 66.7 | 0.1488 | 2.263 | 25.3 |
| 2018 | 8 | 50.0 | 0.1735 | 1.133 | 11.8 |
| 2019 | 10 | 50.0 | 0.1853 | 1.541 | 67.3 |
| 2020 | 6 | 16.7 | -0.3849 | 0.401 | -113.3 |
| 2021 | 11 | 54.5 | 0.0924 | 1.473 | 58.0 |
| 2022 | 6 | 66.7 | 0.4814 | 2.633 | 124.7 |
| 2023 | 6 | 16.7 | -0.1056 | 0.988 | -1.4 |
| 2024 | 8 | 37.5 | -0.1943 | 0.36 | -193.5 |
| 2025 | 5 | 80.0 | 0.508 | 10.058 | 143.1 |

## Walk-forward (expanding folds, z* fixed)

| fold | test | n | win% | expR | PF | net pt |
|--|--|--:|--:|--:|--:|--:|
| 1 | 2017-06-05..2018-11-06 | 9 | 55.6 | 0.173 | 1.371 | 28.1 |
| 2 | 2018-11-07..2020-04-07 | 13 | 38.5 | -0.0113 | 0.936 | -13.2 |
| 3 | 2020-04-08..2021-09-10 | 11 | 45.5 | -0.0272 | 0.896 | -22.6 |
| 4 | 2021-09-13..2023-02-13 | 10 | 60.0 | 0.2894 | 2.23 | 145.3 |
| 5 | 2023-02-14..2024-07-19 | 11 | 36.4 | 0.0738 | 1.693 | 91.6 |
| 6 | 2024-07-22..2025-12-22 | 8 | 50.0 | -0.0575 | 0.526 | -143.5 |

_Learning dataset (every signal + path) written alongside as `orderflow_v1_learning_*.jsonl` for continuous zone re-calibration._