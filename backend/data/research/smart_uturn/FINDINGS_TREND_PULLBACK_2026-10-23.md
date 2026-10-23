# Chanakya Smart Trend-Pullback v1 — mirror replay findings (2026-10-23)
Spec committed first (SPEC_TREND_PULLBACK_2026-10-23.md). Mirror: app/smart_uturn/trend_pullback.py. Data: Kaggle NIFTY 1-minute. Index level, entry at the signal close (optimistic), thirds exit, stop first, flat 15:15, cost 2 index points per round trip.
| period | chart (HTFs) | trades | per day | win | T1 / T2 / T3 reached | gross R per trade | net R per trade | net 95% CI | avg risk (pts) |
|---|---|---|---|---|---|---|---|---|---|
| 2015-19 | 5m (15m, 60m) | 1601 | 1.4 | 41% | 52% / 30% / 18% | +0.093 | -0.050 | -0.111..+0.008 | 17 |
| 2015-19 | 15m (60m, 240m) | 700 | 1.1 | 48% | 39% / 13% / 4% | +0.035 | -0.043 | -0.113..+0.029 | 31 |
| 2020-25 | 5m | 1907 | 1.4 | 41% | 47% / 27% / 14% | +0.046 | -0.024 | -0.077..+0.027 | 36 |
| 2020-25 | 15m | 844 | 1.1 | 45% | 32% / 10% / 3% | -0.008 | -0.046 | -0.108..+0.015 | 64 |
Verdict (pre-registered rule): NO EDGE. Gross expectancy is slightly positive on the 5m chart in both periods (+0.05 to +0.09 R) and about zero on 15m; the 2-point cost (about 0.06-0.12 R at these risk sizes) turns every cell negative and every interval includes 0. T1 is reached about half the time on 5m, T3 about one trade in six.
The Pine dashboard computes the same scoreboard live on the user's own chart; its numbers (win %, average R, target reach rates) are the thing to trust, not the arrows.
Not verified: the Pine file was written without access to TradingView (no compile check here); the Python mirror reproduces its intended logic, not its compiled behaviour.
