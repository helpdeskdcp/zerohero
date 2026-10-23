# Nirnay 1 (trend-day, relaxed risk) vs Nirnay 2 (volume breakout) — findings (2026-10-23)
Spec committed first (SPEC_NIRNAY_COMPARE_2026-10-23.md). Net R per trade after 2 index points per round trip; entry = signal close; thirds 1R/2R/3R; stop cap 10 ATR, max 6 signals/day, stop after 4 losses for the two Nirnay variants.
## NIFTY futures 1-minute with real volume, Jul-Oct 2026 (80 sessions, one regime; 7.8% zero-volume bars, 444 missing minutes)
| chart | shipped PULLBACK | BREAKOUT old caps | NIRNAY 1 | NIRNAY 2 |
|---|---|---|---|---|
| 1m | -0.215 (-0.399..-0.024), n=163 | -0.140 (-0.367..+0.098), n=102 | +0.030 (-0.138..+0.207), n=177 | -0.050 (-0.214..+0.114), n=206 |
| 5m | -0.180 (-0.375..+0.037), n=109 | +0.017 (-0.239..+0.283), n=50 | -0.046 (-0.215..+0.130), n=76 | +0.018 (-0.156..+0.199), n=84 |
## BANKNIFTY futures (80 sessions, 19.5% zero-volume bars)
| 1m | -0.428 (-0.605..-0.249) | -0.035 (-0.322..+0.273) | -0.140 (-0.305..+0.051) | -0.090 (-0.246..+0.069) |
| 5m | -0.259 (-0.438..-0.061) | -0.139 (-0.505..+0.250) | -0.174 (-0.336..-0.004) | -0.070 (-0.234..+0.106) |
## NIFTY index 2015-2025 (no volume): NIRNAY 1 vs the old-cap breakout vs shipped pullback
| P1 5m | pullback -0.050 | old caps -0.020 (n=545) | NIRNAY 1 -0.013 (-0.069..+0.041), n=1130, avg risk 46 pts |
| P1 15m | -0.043 | +0.020 | +0.007 (-0.042..+0.061), n=661, risk 74 pts |
| P2 5m | -0.024 | +0.092 (+0.005..+0.177), n=662 | +0.043 (-0.005..+0.092), n=1355, risk 99 pts |
| P2 15m | -0.046 | +0.040 | +0.013 (-0.032..+0.059), n=758, risk 153 pts |
Verdict (pre-registered rule): NO EDGE for both. Neither Nirnay variant has a net interval above 0 in any cell; both sit near break-even and clearly above the shipped pullback on the futures sample (which is significantly negative on BANKNIFTY).
Reading: relaxing the stop cap / loss cap (Nirnay 1) roughly doubles the trades and 2-3x the risk per trade (46-153 index points) for about the same total net R as the old caps, so net R per trade falls toward zero; on the 80-session futures sample Nirnay 2 is slightly less bad than Nirnay 1 on BANKNIFTY (-0.09/-0.07 vs -0.14/-0.17) and a tie on NIFTY. The volume rule does not show a clear improvement over the plain trend-day rule with this little data. Nothing here supports calling either profitable; the TradingView dashboard scoreboard on the owner's own charts is the deciding evidence.
