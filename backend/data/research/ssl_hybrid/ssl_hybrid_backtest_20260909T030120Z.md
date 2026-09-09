# SSL Hybrid PRO -- backtest (WIN / LOSS in points)

_generated 2026-09-09T03:01:06.434159+00:00 · NIFTY, BANKNIFTY · 15m · 2016-01-01..2025-12-31 · runtime 13.8s_

## VERDICT: **NO-GO**

Do NOT wire into production. On out-of-sample sessions the SSL Hybrid PRO rule set does not clear the points / PF / multi-year bar.

Failed:
- NIFTY: OOS profit factor 1.219 < 1.3
- BANKNIFTY: OOS net -9711.5 points (<=0)
- BANKNIFTY: OOS profit factor 0.746 < 1.3

## Coverage
```
{
 "NIFTY": {
  "source": "kaggle",
  "n_bars": 119188,
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
  "n_bars": 69539,
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
  "n_bars": 80,
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
- 15m bars resampled from Kaggle 1m history (NIFTY, BANKNIFTY).
- Volume is 0/absent in all multi-year history -> VWAP is a session-anchored cumulative HLC3 mean (price-VWAP proxy); the 5-pt volume score is inert.
- SENSEX: only market_history.db INDEX 1m (~4 sessions) -> INSUFFICIENT_SAMPLE.


## NIFTY

2599 sessions 2016-01-01..2025-12-24 · 4571 signals · 1362 trades (0.524/session) · has_volume=False

| slice | n | W | L | win% | net pts | avg win | avg loss | PF | maxDD pts | exp R |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| all | 1362 | 674 | 688 | 49.5 | 2017.9 | 44.11 | -40.28 | 1.073 | -1637.3 | 0.0117 |
| in_sample | 935 | 454 | 481 | 48.6 | -238.4 | 37.87 | -36.24 | 0.986 | -1637.3 | -0.0216 |
| out_of_sample | 427 | 220 | 207 | 51.5 | 2256.3 | 56.99 | -49.67 | 1.219 | -555.2 | 0.0847 |

| year | n | W | L | net pts | PF |
|---|--:|--:|--:|--:|--:|
| 2016 | 154 | 72 | 82 | -191.9 | 0.898 |
| 2017 | 122 | 59 | 63 | -137.7 | 0.893 |
| 2018 | 109 | 44 | 65 | -439.6 | 0.751 |
| 2019 | 129 | 61 | 68 | -336.8 | 0.843 |
| 2020 | 123 | 64 | 59 | 529.0 | 1.181 |
| 2021 | 146 | 77 | 69 | 468.5 | 1.143 |
| 2022 | 155 | 77 | 78 | -264.6 | 0.938 |
| 2023 | 142 | 72 | 70 | 389.5 | 1.143 |
| 2024 | 148 | 71 | 77 | 893.2 | 1.215 |
| 2025 | 134 | 77 | 57 | 1108.2 | 1.34 |

by side: LONG net 773.9 pts (745) · SHORT net 1244.0 pts (617)
exit reasons: {"SESSION_END": {"n": 460, "sum_points": 17066.5}, "STOP": {"n": 606, "sum_points": -26381.9}, "BREAKEVEN": {"n": 185, "sum_points": 2601.8}, "TRAIL_BE": {"n": 21, "sum_points": 845.9}, "TARGET_T3": {"n": 86, "sum_points": 8003.0}, "OPPOSITE": {"n": 4, "sum_points": -117.3}}

## BANKNIFTY

2477 sessions 2016-01-01..2025-12-30 · 4149 signals · 1380 trades (0.557/session) · has_volume=False

| slice | n | W | L | win% | net pts | avg win | avg loss | PF | maxDD pts | exp R |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| all | 1380 | 638 | 742 | 46.2 | -13495.3 | 134.46 | -133.8 | 0.864 | -16258.0 | -0.0511 |
| in_sample | 943 | 447 | 496 | 47.4 | -3783.7 | 128.1 | -123.08 | 0.938 | -6346.6 | -0.0287 |
| out_of_sample | 437 | 191 | 246 | 43.7 | -9711.5 | 149.34 | -155.43 | 0.746 | -10225.3 | -0.0996 |

| year | n | W | L | net pts | PF |
|---|--:|--:|--:|--:|--:|
| 2016 | 122 | 53 | 69 | -1061.7 | 0.793 |
| 2017 | 126 | 60 | 66 | -168.4 | 0.957 |
| 2018 | 120 | 62 | 58 | -218.1 | 0.956 |
| 2019 | 132 | 68 | 64 | 1575.1 | 1.231 |
| 2020 | 143 | 68 | 75 | -811.4 | 0.942 |
| 2021 | 135 | 63 | 72 | 488.6 | 1.043 |
| 2022 | 167 | 73 | 94 | -3887.1 | 0.744 |
| 2023 | 140 | 61 | 79 | -908.0 | 0.909 |
| 2024 | 138 | 54 | 84 | -7047.1 | 0.547 |
| 2025 | 157 | 76 | 81 | -1457.2 | 0.883 |

by side: LONG net -4270.9 pts (749) · SHORT net -9224.4 pts (631)
exit reasons: {"TARGET_T3": {"n": 85, "sum_points": 24163.5}, "STOP": {"n": 631, "sum_points": -93840.9}, "BREAKEVEN": {"n": 176, "sum_points": 7762.9}, "SESSION_END": {"n": 474, "sum_points": 46423.5}, "TRAIL_BE": {"n": 14, "sum_points": 1995.8}}