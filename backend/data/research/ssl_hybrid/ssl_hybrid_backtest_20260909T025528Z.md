# SSL Hybrid PRO -- backtest (WIN / LOSS in points)

_generated 2026-09-09T02:55:20.480832+00:00 · NIFTY, BANKNIFTY · 60m · 2016-01-01..2025-12-31 · runtime 7.6s_

## VERDICT: **NO-GO**

Do NOT wire into production. On out-of-sample sessions the SSL Hybrid PRO rule set does not clear the points / PF / multi-year bar.

Failed:
- NIFTY: OOS net -2074.3 points (<=0)
- NIFTY: OOS profit factor 0.875 < 1.3
- NIFTY: net-positive in only 1 year(s) (<2)
- BANKNIFTY: OOS net -10053.3 points (<=0)
- BANKNIFTY: OOS profit factor 0.768 < 1.3

## Coverage
```
{
 "NIFTY": {
  "source": "kaggle",
  "n_bars": 32120,
  "sessions": 4568,
  "range": [
   "2008-01-01",
   "2025-12-24"
  ],
  "has_volume": false,
  "verdict": "OOS_CAPABLE"
 },
 "BANKNIFTY": {
  "source": "kaggle",
  "n_bars": 19484,
  "sessions": 2793,
  "range": [
   "2015-01-09",
   "2026-04-22"
  ],
  "has_volume": false,
  "verdict": "OOS_CAPABLE"
 },
 "SENSEX": {
  "source": "market_history.db",
  "n_bars": 23,
  "sessions": 4,
  "range": [
   "2026-09-03",
   "2026-09-08"
  ],
  "has_volume": false,
  "verdict": "INSUFFICIENT_SAMPLE"
 }
}
```
- 60m bars resampled from Kaggle 1m history (NIFTY, BANKNIFTY).
- Volume is 0/absent in all multi-year history -> VWAP is a session-anchored cumulative HLC3 mean (price-VWAP proxy); the 5-pt volume score is inert.
- SENSEX: only market_history.db INDEX 1m (~4 sessions) -> INSUFFICIENT_SAMPLE.


## NIFTY

2599 sessions 2016-01-01..2025-12-24 · 1706 signals · 923 trades (0.355/session) · has_volume=False

| slice | n | W | L | win% | net pts | avg win | avg loss | PF | maxDD pts | exp R |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| all | 923 | 406 | 517 | 44.0 | -5892.0 | 95.54 | -86.42 | 0.868 | -6478.7 | -0.06 |
| in_sample | 666 | 284 | 382 | 42.6 | -3817.8 | 85.57 | -73.61 | 0.864 | -4374.8 | -0.0771 |
| out_of_sample | 257 | 122 | 135 | 47.5 | -2074.3 | 118.75 | -122.68 | 0.875 | -2698.2 | -0.0159 |

| year | n | W | L | net pts | PF |
|---|--:|--:|--:|--:|--:|
| 2016 | 76 | 27 | 49 | -880.9 | 0.631 |
| 2017 | 84 | 37 | 47 | -32.2 | 0.983 |
| 2018 | 85 | 43 | 42 | 754.8 | 1.315 |
| 2019 | 122 | 50 | 72 | -987.3 | 0.707 |
| 2020 | 107 | 40 | 67 | -1191.8 | 0.797 |
| 2021 | 93 | 42 | 51 | -293.8 | 0.945 |
| 2022 | 99 | 45 | 54 | -1186.6 | 0.827 |
| 2023 | 84 | 44 | 40 | -391.7 | 0.896 |
| 2024 | 87 | 42 | 45 | -671.8 | 0.899 |
| 2025 | 86 | 36 | 50 | -1010.8 | 0.836 |

by side: LONG net 3408.1 pts (512) · SHORT net -9300.1 pts (411)
exit reasons: {"BREAKEVEN": {"n": 157, "sum_points": 4429.2}, "TARGET_T3": {"n": 150, "sum_points": 27041.2}, "STOP": {"n": 481, "sum_points": -43870.3}, "TRAIL_BE": {"n": 55, "sum_points": 4468.4}, "SESSION_END": {"n": 78, "sum_points": 1936.9}, "OPPOSITE": {"n": 1, "sum_points": -50.1}, "EOD": {"n": 1, "sum_points": 152.7}}

## BANKNIFTY

2477 sessions 2016-01-01..2025-12-30 · 1517 signals · 774 trades (0.312/session) · has_volume=False

| slice | n | W | L | win% | net pts | avg win | avg loss | PF | maxDD pts | exp R |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| all | 774 | 355 | 419 | 45.9 | -11721.1 | 304.85 | -286.26 | 0.902 | -15090.7 | -0.0304 |
| in_sample | 546 | 261 | 285 | 47.8 | -1667.8 | 286.92 | -268.61 | 0.978 | -8540.2 | 0.0094 |
| out_of_sample | 228 | 94 | 134 | 41.2 | -10053.3 | 354.65 | -323.81 | 0.768 | -10625.0 | -0.1258 |

| year | n | W | L | net pts | PF |
|---|--:|--:|--:|--:|--:|
| 2016 | 71 | 28 | 43 | -2064.2 | 0.685 |
| 2017 | 79 | 35 | 44 | -1057.7 | 0.831 |
| 2018 | 87 | 40 | 47 | -507.4 | 0.943 |
| 2019 | 61 | 36 | 25 | 1043.4 | 1.144 |
| 2020 | 78 | 42 | 36 | 4359.2 | 1.32 |
| 2021 | 89 | 36 | 53 | -5724.7 | 0.722 |
| 2022 | 81 | 44 | 37 | 2283.5 | 1.171 |
| 2023 | 75 | 32 | 43 | -3799.8 | 0.697 |
| 2024 | 74 | 34 | 40 | -596.5 | 0.959 |
| 2025 | 79 | 28 | 51 | -5657.1 | 0.649 |

by side: LONG net 12747.7 pts (419) · SHORT net -24468.8 pts (355)
exit reasons: {"BREAKEVEN": {"n": 158, "sum_points": 14636.1}, "TARGET_T3": {"n": 143, "sum_points": 78488.7}, "STOP": {"n": 415, "sum_points": -119309.6}, "TRAIL_BE": {"n": 51, "sum_points": 13233.7}, "OPPOSITE": {"n": 1, "sum_points": -150.7}, "SESSION_END": {"n": 6, "sum_points": 1380.7}}