# Stop-hunt reclaim entry — mirror replay findings (2026-10-23)
Spec committed first (SPEC_HUNT_ENTRY_2026-10-23.md). Kaggle NIFTY 1-minute, PULLBACK signal unchanged, cost 2 index points per round trip. The volume gate (no >= 2x spike in the last 7 candles) CANNOT be tested: this index data has no volume. So this is the sweep-and-reclaim entry without the volume filter.
| period | chart | entry | trades | gross R | net R | net 95% CI | avg risk (pts) | stop share |
|---|---|---|---|---|---|---|---|---|
| 2015-19 | 5m | original (signal close) | 1601 | +0.093 | -0.050 | -0.111..+0.008 | 17 | 60% |
| 2015-19 | 5m | HUNT (stop = sweep extreme) | 516 | +0.097 | -0.131 | -0.235..-0.028 | 11 | 63% |
| 2015-19 | 5m | HUNT, stop = 25% of signal risk | 580 | -0.065 | -0.566 | -0.666..-0.467 | 4.5 | 77% |
| 2015-19 | 15m | original | 700 | +0.035 | -0.043 | -0.113..+0.029 | 31 | 40% |
| 2015-19 | 15m | HUNT | 138 | +0.088 | -0.027 | -0.199..+0.151 | 23 | 43% |
| 2015-19 | 15m | HUNT 25% | 156 | +0.105 | -0.144 | -0.348..+0.064 | 9 | 67% |
| 2020-25 | 5m | original | 1907 | +0.046 | -0.024 | -0.077..+0.027 | 36 | 58% |
| 2020-25 | 5m | HUNT | 622 | +0.038 | -0.073 | -0.166..+0.021 | 23 | 63% |
| 2020-25 | 5m | HUNT 25% | 705 | +0.023 | -0.221 | -0.314..-0.130 | 9 | 73% |
| 2020-25 | 15m | original | 844 | -0.008 | -0.046 | -0.108..+0.015 | 64 | 37% |
| 2020-25 | 15m | HUNT | 169 | -0.038 | -0.093 | -0.244..+0.061 | 51 | 51% |
| 2020-25 | 15m | HUNT 25% | 187 | +0.218 | +0.098 | -0.086..+0.280 | 19 | 62% |
Verdict (pre-registered): NO IMPROVEMENT. HUNT keeps the per-trade gross edge about the same (+0.04..+0.10 R on 5m) but takes only about a third of the trades (most setups never sweep their stop level: price either goes to the target first or the setup expires), and its stop is tighter (11-23 points instead of 17-36), so the fixed 2-point cost weighs more and net R gets worse in 3 of 4 cells (15m 2015-19 is the exception, still inside zero). The 25%-of-risk stop is far too tight for 1-5 minute index bars (stops 4-9 points, 62-77% stopped out); its one positive cell (2020-25 15m, n=187) has an interval including 0 and is not credible next to the other five.
Not tested: the volume condition, MCX instruments, options premium.
