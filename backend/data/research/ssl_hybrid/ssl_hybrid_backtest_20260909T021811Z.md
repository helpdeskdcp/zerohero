# SSL Hybrid PRO -- backtest (WIN / LOSS in points)

_generated 2026-09-09T02:17:58.263808+00:00 · NIFTY, BANKNIFTY, SENSEX · 15m · 2016-01-01..2025-12-31 · runtime 13.2s_

## VERDICT: **NO-GO**

Do NOT wire into production. On out-of-sample sessions the SSL Hybrid PRO rule set does not clear the points / PF / multi-year bar.

Failed:
- NIFTY: OOS net -9862.5 points (<=0)
- NIFTY: OOS profit factor 0.656 < 1.3
- NIFTY: net-positive in only 0 year(s) (<2)
- BANKNIFTY: OOS net -35530.2 points (<=0)
- BANKNIFTY: OOS profit factor 0.581 < 1.3
- BANKNIFTY: net-positive in only 0 year(s) (<2)
- SENSEX: NO_DATA

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

2599 sessions 2016-01-01..2025-12-24 · 5022 signals · 3373 trades (1.298/session) · has_volume=False

| slice | n | W | L | win% | net pts | avg win | avg loss | PF | maxDD pts | exp R |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| all | 3373 | 1282 | 2091 | 38.0 | -26464.8 | 40.54 | -37.51 | 0.663 | -26809.1 | -0.1859 |
| in_sample | 2393 | 913 | 1480 | 38.1 | -16602.3 | 36.32 | -33.63 | 0.666 | -17213.7 | -0.1886 |
| out_of_sample | 980 | 369 | 611 | 37.6 | -9862.5 | 50.98 | -46.93 | 0.656 | -10047.0 | -0.1794 |

| year | n | W | L | net pts | PF |
|---|--:|--:|--:|--:|--:|
| 2016 | 354 | 134 | 220 | -1401.4 | 0.692 |
| 2017 | 332 | 110 | 222 | -1794.2 | 0.547 |
| 2018 | 329 | 132 | 197 | -818.0 | 0.832 |
| 2019 | 357 | 131 | 226 | -2814.1 | 0.581 |
| 2020 | 336 | 136 | 200 | -2039.0 | 0.773 |
| 2021 | 335 | 145 | 190 | -2353.9 | 0.737 |
| 2022 | 357 | 127 | 230 | -5572.3 | 0.535 |
| 2023 | 325 | 127 | 198 | -1908.2 | 0.729 |
| 2024 | 325 | 120 | 205 | -3795.0 | 0.651 |
| 2025 | 323 | 120 | 203 | -3968.7 | 0.623 |

by side: LONG net -11619.5 pts (1756) · SHORT net -14845.3 pts (1617)
exit reasons: {"SESSION_END": {"n": 1128, "sum_points": 28205.7}, "STOP": {"n": 1697, "sum_points": -74174.2}, "BREAKEVEN": {"n": 352, "sum_points": 4309.0}, "TARGET_T3": {"n": 155, "sum_points": 14098.4}, "TRAIL_BE": {"n": 35, "sum_points": 1343.6}, "OPPOSITE": {"n": 6, "sum_points": -247.2}}

## BANKNIFTY

2477 sessions 2016-01-01..2025-12-30 · 4584 signals · 3012 trades (1.216/session) · has_volume=False

| slice | n | W | L | win% | net pts | avg win | avg loss | PF | maxDD pts | exp R |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| all | 3012 | 1181 | 1831 | 39.2 | -77790.2 | 128.73 | -125.52 | 0.662 | -78122.4 | -0.1673 |
| in_sample | 2094 | 837 | 1257 | 40.0 | -42260.0 | 122.7 | -115.32 | 0.708 | -43505.2 | -0.1509 |
| out_of_sample | 918 | 344 | 574 | 37.5 | -35530.2 | 143.41 | -147.84 | 0.581 | -35664.7 | -0.2047 |

| year | n | W | L | net pts | PF |
|---|--:|--:|--:|--:|--:|
| 2016 | 289 | 109 | 180 | -4789.3 | 0.621 |
| 2017 | 271 | 100 | 171 | -4224.0 | 0.6 |
| 2018 | 310 | 125 | 185 | -5248.8 | 0.672 |
| 2019 | 300 | 133 | 167 | -3114.1 | 0.827 |
| 2020 | 311 | 126 | 185 | -6199.3 | 0.801 |
| 2021 | 285 | 114 | 171 | -7339.6 | 0.718 |
| 2022 | 331 | 130 | 201 | -11782.9 | 0.621 |
| 2023 | 294 | 120 | 174 | -4696.0 | 0.777 |
| 2024 | 305 | 103 | 202 | -19020.5 | 0.467 |
| 2025 | 316 | 121 | 195 | -11375.7 | 0.589 |

by side: LONG net -37412.7 pts (1594) · SHORT net -40377.4 pts (1418)
exit reasons: {"SESSION_END": {"n": 1080, "sum_points": 77319.1}, "BREAKEVEN": {"n": 313, "sum_points": 13712.9}, "TARGET_T3": {"n": 141, "sum_points": 40005.7}, "STOP": {"n": 1438, "sum_points": -211755.6}, "TRAIL_BE": {"n": 29, "sum_points": 4213.0}, "OPPOSITE": {"n": 11, "sum_points": -1285.2}}

## SENSEX

`NO_DATA`

---

## Timeframe sensitivity (NIFTY + BANKNIFTY, 2016-2025, same rules)

| TF | NIFTY trades | NIFTY OOS net pts | NIFTY OOS PF | BANKNIFTY trades | BANKNIFTY OOS net pts | BANKNIFTY OOS PF |
|---|--:|--:|--:|--:|--:|--:|
| 5m  | 7326 | -12111 | 0.72 | 7077 | -45249 | 0.63 |
| 15m | 3373 |  -9863 | 0.66 | 3012 | -35530 | 0.58 |
| 30m | 2281 |  -9349 | 0.62 | 1353 |  -6798 | 0.87 |
| 60m | 1002 |  -1071 | 0.94 |  830 |  -9489 | 0.79 |

Every timeframe is net-negative on both indices. 60m is the least-bad for
NIFTY (still a loss, PF 0.94); BANKNIFTY stays firmly negative everywhere.

## Why it loses (structure, not tuning)

- Win rate is 37-39% across every timeframe. With the ATR(14)x1.5 stop and
  1/3-1/3-1/3 exits at RR 1 / 2 / 3, the blended reward is ~1.3R, so
  break-even needs ~44%+. The rule set delivers ~38%.
- STOP exits alone: -74,174 pts (NIFTY 15m) / -211,756 pts (BANKNIFTY 15m).
  The positive buckets (SESSION_END, TARGET_T3, BREAKEVEN) do not come close
  to covering them.
- All 10 calendar years are net-negative for both symbols -- not a regime
  issue, the edge is absent throughout. IS and OOS agree (NIFTY expR -0.189 ->
  -0.179), so nothing is overfit.

## Methodology caveats (read before concluding the port is wrong)

1. Volume / VWAP: NSE cash-index history has zero volume. The Pine's
   strongBull REQUIRES aboveVWAP; on a zero-volume feed ta.vwap is na and the
   strategy fires nothing. We substitute a session-anchored cumulative HLC3
   mean (price-VWAP proxy); the 5-pt volume-score component is inert. A
   real-volume run (NIFTY futures) is impossible here -- only ~5 such sessions
   exist in market_history.db.
2. The Pine is an INDICATOR, not a strategy tester. It draws entry / SL /
   T1-T3 lines and a confidence label; it never simulates the outcome or
   reports realised win/loss. "SL/targets look perfect" is a read of the
   drawn levels, not a measured edge. This backtest is the first bar-by-bar
   accounting of the rule set.
3. Trade management here is one reasonable interpretation. With a 38% hit
   rate, no exit policy recovers a positive expectancy.

## Verdict: NO-GO -- do not wire to live. live_trading stays false.
