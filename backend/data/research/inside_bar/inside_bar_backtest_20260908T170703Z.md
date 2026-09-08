# Inside-Bar 2m strategy -- backtest

_generated 2026-09-08T17:06:35.390258+00:00 · NIFTY, BANKNIFTY, SENSEX · 2016-01-01..2025-12-31 · runtime 27.7s_

## VERDICT: **NO-GO**

Do NOT wire into production. On out-of-sample sessions the inside-bar 2m rule set does not clear the expectancy / PF / multi-year-stability bar. Consistent with the other strategy audits this session.

Failed:
- NIFTY: OOS expectancy -0.0555R not > cost
- NIFTY: OOS profit factor 0.899 < 1.3
- NIFTY: positive in only 1 calendar year(s) (<2)
- BANKNIFTY: OOS expectancy -0.1941R not > cost
- BANKNIFTY: OOS profit factor 0.678 < 1.3
- BANKNIFTY: positive in only 0 calendar year(s) (<2)
- SENSEX: INSUFFICIENT_SAMPLE

## Coverage
```
{
 "NIFTY": {
  "source": "kaggle",
  "n_2m_bars": 854368,
  "range": [
   "2008-01-01",
   "2025-12-24"
  ],
  "sessions": 4568,
  "verdict": "OOS_CAPABLE"
 },
 "BANKNIFTY": {
  "source": "kaggle",
  "n_2m_bars": 522867,
  "range": [
   "2015-01-09",
   "2026-04-22"
  ],
  "sessions": 2793,
  "verdict": "OOS_CAPABLE"
 },
 "SENSEX": {
  "source": "market_history.db",
  "n_2m_bars": 573,
  "range": [
   "2026-09-03",
   "2026-09-08"
  ],
  "sessions": 4,
  "verdict": "INSUFFICIENT_SAMPLE"
 }
}
```
- NIFTY / BANKNIFTY: 2m resampled from Kaggle 1m history -> real walk-forward OOS.
- SENSEX: only market_history.db INDEX 1m (~4 sessions) -- INSUFFICIENT_SAMPLE; the Kaggle SENSEX file (prathamsatani) is DAILY, not intraday.
- Cash-index volume is 0 everywhere -- this strategy uses price geometry + EMA only.
- 1m timestamps are treated as naive session-clock; intraday session rules (no-entry-after / hard-exit) use hour:minute, so this is exact.


## NIFTY

2599 sessions 2016-01-01..2025-12-24 · 3126 setups triggered · 2906 trades (1.12/day)
- **all** n=2906 win=0.4539 expR=-0.0765 PF=0.86 maxDD=-251.518R avgW=1.034 avgL=-1.0
- **in_sample** n=1902 win=0.4543 expR=-0.0876 PF=0.839 maxDD=-193.147R avgW=1.008 avgL=-1.0
- **out_of_sample** n=1004 win=0.4532 expR=-0.0555 PF=0.899 maxDD=-84.37R avgW=1.084 avgL=-1.0

| year | n | win | expR | PF | maxDD R |
|---|--:|--:|--:|--:|--:|
| 2016 | 220 | 0.4727 | -0.0246 | 0.953 | -27.337 |
| 2017 | 182 | 0.4121 | -0.2032 | 0.654 | -38.658 |
| 2018 | 218 | 0.4817 | -0.0169 | 0.967 | -15.009 |
| 2019 | 262 | 0.4695 | -0.0624 | 0.882 | -28.017 |
| 2020 | 291 | 0.4021 | -0.2158 | 0.639 | -70.792 |
| 2021 | 384 | 0.4714 | -0.0738 | 0.86 | -48.33 |
| 2022 | 353 | 0.4561 | -0.052 | 0.904 | -33.669 |
| 2023 | 318 | 0.4811 | 0.0294 | 1.057 | -21.334 |
| 2024 | 326 | 0.4294 | -0.0901 | 0.842 | -46.665 |
| 2025 | 352 | 0.4545 | -0.0862 | 0.842 | -42.655 |

by side: LONG {'n': 1594, 'win_rate': 0.436, 'expectancy_R': -0.1228, 'profit_factor': 0.782, 'avg_win_R': 1.012, 'avg_loss_R': -1.0, 'max_drawdown_R': -212.515, 'mae_R_mean': -1.145, 'mfe_R_mean': 1.271} · SHORT {'n': 1312, 'win_rate': 0.4756, 'expectancy_R': -0.0202, 'profit_factor': 0.961, 'avg_win_R': 1.059, 'avg_loss_R': -0.999, 'max_drawdown_R': -46.637, 'mae_R_mean': -1.094, 'mfe_R_mean': 1.441}
target-R hit rates: {"reached_1R": 0.481, "reached_2R": 0.249, "reached_3R": 0.125, "reached_4R": 0.067, "stopped_pre_1R": 0.519}
exit reasons: {"STOP": {"n": 1508, "sum_R": -1508.01}, "BREAKEVEN": {"n": 669, "sum_R": 134.97}, "TRAIL": {"n": 544, "sum_R": 729.67}, "TARGET_4R": {"n": 175, "sum_R": 408.36}, "TIME_1PM": {"n": 10, "sum_R": 12.75}}

## BANKNIFTY

2477 sessions 2016-01-01..2025-12-30 · 3592 setups triggered · 3336 trades (1.35/day)
- **all** n=3336 win=0.4251 expR=-0.1424 PF=0.752 maxDD=-494.604R avgW=1.018 avgL=-1.0
- **in_sample** n=2424 win=0.4352 expR=-0.1229 PF=0.782 maxDD=-310.898R avgW=1.015 avgL=-1.0
- **out_of_sample** n=912 win=0.398 expR=-0.1941 PF=0.678 maxDD=-196.374R avgW=1.025 avgL=-1.0

| year | n | win | expR | PF | maxDD R |
|---|--:|--:|--:|--:|--:|
| 2016 | 330 | 0.4303 | -0.1444 | 0.747 | -60.643 |
| 2017 | 252 | 0.4127 | -0.2222 | 0.622 | -59.337 |
| 2018 | 283 | 0.3887 | -0.2497 | 0.592 | -70.993 |
| 2019 | 341 | 0.4633 | -0.0029 | 0.995 | -15.001 |
| 2020 | 426 | 0.4249 | -0.1557 | 0.729 | -71.0 |
| 2021 | 406 | 0.4458 | -0.1181 | 0.787 | -59.268 |
| 2022 | 387 | 0.4651 | -0.0207 | 0.961 | -42.999 |
| 2023 | 296 | 0.3615 | -0.2658 | 0.584 | -90.997 |
| 2024 | 305 | 0.4164 | -0.1575 | 0.73 | -52.709 |
| 2025 | 310 | 0.4129 | -0.1634 | 0.722 | -63.67 |

by side: LONG {'n': 1681, 'win_rate': 0.4271, 'expectancy_R': -0.1396, 'profit_factor': 0.756, 'avg_win_R': 1.014, 'avg_loss_R': -1.0, 'max_drawdown_R': -255.346, 'mae_R_mean': -1.191, 'mfe_R_mean': 1.402} · SHORT {'n': 1655, 'win_rate': 0.423, 'expectancy_R': -0.1452, 'profit_factor': 0.748, 'avg_win_R': 1.021, 'avg_loss_R': -1.0, 'max_drawdown_R': -245.593, 'mae_R_mean': -1.108, 'mfe_R_mean': 1.328}
target-R hit rates: {"reached_1R": 0.458, "reached_2R": 0.231, "reached_3R": 0.119, "reached_4R": 0.061, "stopped_pre_1R": 0.542}
exit reasons: {"STOP": {"n": 1807, "sum_R": -1807.0}, "TARGET_4R": {"n": 188, "sum_R": 438.7}, "TRAIL": {"n": 580, "sum_R": 741.99}, "BREAKEVEN": {"n": 758, "sum_R": 145.98}, "TIME_1PM": {"n": 3, "sum_R": 5.39}}

## SENSEX

`INSUFFICIENT_SAMPLE` (0 sessions, 0)