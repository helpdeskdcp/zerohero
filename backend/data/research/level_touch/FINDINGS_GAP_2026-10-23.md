# H-GAP1 opening gap fade/continuation — findings (2026-10-23)
Spec SPEC_GAP_2026-10-23.md committed before the result. Code run_gap.py, numbers results_gap.json. Gap days = |gap| >= 0.5 sigma_d (20-session sd); entry 09:29 close, exit 15:14 close.

| cell | gap days | mean CONT (bps) | 95% CI | hit CONT>0 | gap filled intraday |
|---|---|---|---|---|---|
| NIFTY P1 | 386 | +1.9 | -5.4..+9.2 | 53% | 43% |
| NIFTY P2 | 572 | +3.4 | -5.8..+11.9 | 57% | 35% |
| BANKNIFTY P1 | 298 | +3.4 | -7.7..+14.5 | 55% | 44% |
| BANKNIFTY P2 | 507 | -1.2 | -14.5..+11.3 | 50% | 38% |

Verdict: NO GAP EDGE. Sign differs across cells, every CI contains 0, no cell reaches 8 bps. Neither fading nor following a gap moves the index measurably from 09:30 to the close. Buckets (descriptive, cannot rescue): the small gaps lean slightly positive, the 1-2 sigma gaps slightly negative, n for >=2 sigma is 14-26 per cell, noise.
Adds to the earlier NO-GOs: price-only index setups tested so far (level breakout/reversal, gap, trend, reversion, opening range, expiry) show no durable edge in 11 years of NIFTY/BANKNIFTY 1-minute data.
