# SSL Hybrid PRO -- TRAINED BASELINE (SSL1 = 100)

_Recorded 2026-09-09. Config: SSL1=100, SSL2=5, Exit=15, Baseline=60, EMA=200,
HMA=55, RSI=14, ADX=14, ATR=14, ATR stop x1.5, T1/T2/T3 = RR 1/2/3, thirds
scale-out, SL->BE after first partial, opposite-signal + session-end flat.
Data: 2m-... resampled Kaggle 1m (NIFTY 2008-2025, BANKNIFTY 2015-2024),
price-VWAP proxy (zero cash-index volume). OOS = most recent 30% of sessions.
This is stage 1: reproduce + record. No filter changes yet._

## 15m -- headline

| symbol | trades | W | L | win% | net pts | avg win | avg loss | PF | maxDD pts | exp R |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| **NIFTY ALL** | 3142 | 1201 | 1941 | 38.22 | -23,481 | 40.89 | -37.40 | 0.677 | -23,830 | -0.178 |
| NIFTY in-sample | 2237 | 852 | 1385 | 38.09 | -15,426 | 36.32 | -33.48 | 0.667 | -15,992 | -0.185 |
| **NIFTY OOS** | 905 | 349 | 556 | 38.56 | -8,055 | 52.05 | -47.16 | 0.693 | -8,147 | -0.161 |
| **BANKNIFTY ALL** | 2778 | 1111 | 1667 | 39.99 | -67,042 | 128.92 | -126.14 | 0.681 | -67,281 | -0.156 |
| BANKNIFTY in-sample | 1923 | 790 | 1133 | 41.08 | -34,946 | 122.34 | -116.15 | 0.734 | -35,947 | -0.136 |
| **BANKNIFTY OOS** | 855 | 321 | 534 | 37.54 | -32,096 | 145.13 | -147.35 | 0.592 | -32,231 | -0.200 |

SENSEX: NO_DATA (no in-range intraday history).

## 15m -- by side / by year / by exit

NIFTY  LONG net -10,830 (1680 tr, 38.4% win) · SHORT net -12,651 (1462 tr, 38.0%)
NIFTY  by-year net pts (all negative): 2016 -1453 · 2017 -1870 · 2018 -853 · 2019 -2816 · 2020 -2073 · 2021 -2012 · 2022 -4604 · 2023 -1701 · 2024 -2651 · 2025 -3449
NIFTY  exits: STOP -68,879 (1579) · SESSION_END +26,906 (1055) · TARGET_T3 +13,278 (145) · BREAKEVEN +4,155 (323) · TRAIL_BE +1,241 (35) · OPPOSITE -182 (5)

BANKNIFTY LONG net -31,519 (1492 tr, 40.6%) · SHORT net -35,523 (1286 tr, 39.3%)
BANKNIFTY by-year net pts (all negative): 2016 -4774 · 2017 -3452 · 2018 -4043 · 2019 -1627 · 2020 -4942 · 2021 -5439 · 2022 -11,108 · 2023 -4232 · 2024 -16,764 · 2025 -10,662
BANKNIFTY exits: STOP -194,230 (1322) · SESSION_END +73,272 (1010) · TARGET_T3 +38,255 (135) · BREAKEVEN +12,160 (283) · TRAIL_BE +3,845 (25) · OPPOSITE -343 (3)

## Timeframe grid (NIFTY / BANKNIFTY, 2016-2025)

| TF | NIFTY ALL win% / net pts / PF | NIFTY OOS net pts / PF | BANKNIFTY ALL win% / net pts / PF | BANKNIFTY OOS net pts / PF |
|---|---|---|---|---|
| 5m  | 39.3% / -28,656 / 0.72 | -9,958 / 0.75 | 38.5% / -100,959 / 0.67 | -37,987 / 0.66 |
| 15m | 38.2% / -23,481 / 0.68 | -8,055 / 0.69 | 40.0% / -67,042 / 0.68 | -32,096 / 0.59 |
| 30m | 41.4% / -18,205 / 0.68 | -8,884 / 0.61 | 44.7% / -20,635 / 0.86 | -7,225 / 0.86 |
| 60m | 44.0% / -5,892 / 0.87  | -2,074 / 0.88 | 45.9% / -11,721 / 0.90 | -10,053 / 0.77 |

## Verdict at baseline: **NO-GO**

SSL1=100 vs SSL1=60: marginally less bad on NIFTY 15m (OOS -8,055 vs -9,863 pts)
and BANKNIFTY 15m (OOS -32,096 vs -35,530), win rate unchanged at ~38%. Every
timeframe, every calendar year, both indices: net-negative. STOP exits dominate
the loss. IS ~= OOS -> not overfit; the raw signal has no directional edge.

## Next stages (per instruction)

2. Optimise the confirmation/filter layer for max statistically-valid signal
   precision with **SSL1=100 fixed**. Target: fewer, cleaner signals -- raise
   win% / PF without curve-fitting; validate on OOS + walk-forward.
3. Only then: robustness sweep SSL1 in {80, 90, 100, 110, 120}, OOS +
   walk-forward. Never pick a value for in-sample win rate alone.
