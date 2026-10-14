# Hybrid Market Profile v13.0 — backtest correctness audit (2026-10-14)

Research only; index-level R (move ÷ planned risk), no options/lots/margin. Code commit `39a3412`. Data = research cache of captured 1-minute bars
(NIFTY through 2026-10-12 15:29, BANKNIFTY/SENSEX through 2026-10-14, MCX futures from 2026-07). Splits TRAIN 2017-06..2021-12 / VALID 2022-2023 / OOS 2024-.. — **nothing was tuned in this audit**.

## 1. Verdicts
| question | status | one-line evidence |
|---|---|---|
| Previously reported baseline reproducible | **VERIFIED** | With the legacy defects switched back on, NIFTY 5m fixed config gives n=2454/1072/1435, gross +0.032/+0.047/+0.044 R, net(2 bps) −0.125/−0.131/−0.138 R — identical to the stored report (§2). |
| Backtest mechanics (fills, ordering, gaps, EOD, one position, partials) | **VERIFIED** | Engine R equals an independent tick-level path walker trade-for-trade when bars see every step (`tests/test_hybrid_mp_bias_world.py`); invariants tested on random data (§3). |
| Implementation defects in the backtester | **FAILED → FIXED** | Weekday off-by-one dropped every Friday (≈20% of sessions); pivot tie rule; next-bar peek in EOD logic (§3, D1–D4 in the parity report). |
| Unexplained positive random-walk expectancy (+0.03…0.04 R) | **EXPLAINED — not an implementation defect** | It is the OHLC-sampling artefact: bar-level minus true-path R decays monotonically to 0 as bars see more of the path; true-path expectancy ≈ 0 (§4). |
| Baseline strategy has a tradable edge | **FAILED** | gross ≈ null, net negative in every split and year (§2, §7). |
| Any SMC filter adds validated value | **FAILED** | 9 variants, none positive net in any split with CI excluding 0 net; the one OOS gross CI excluding 0 (displacement) is TRAIN/VALID-inconsistent and net-negative; "all five" yields 0 trades (§6). |
| Real-trading cost realism (spread/slippage/commission/partial fills) | **PENDING** | A decomposed cost parameter and sensitivity table exist (§7); no measured broker costs, no queue/partial-fill model for index options. |
| Options / futures / equities instrument mapping | **PENDING (not testable with the data)** | Only INDEX (NIFTY, BANKNIFTY, SENSEX) and MCX FUTURE (CRUDEOIL, NATURALGAS, 3.5 months) exist; no equity or NSE futures bars; MCX series may contain contract-roll jumps (+10.3% overnight on 2026-07-14). |

## 2. Phase 1 — reproduce, then isolate (NIFTY 5m, `fixed_params`, `scripts/hybrid_mp_audit_baseline.py`)
```
data: NIFTY INDEX 1m, 931831 bars, first=1496288700 last=1791799140; chart 5m bars=173528

[LEGACY (weekday off-by-one + pivot ties=allow)] trades=4961
   TRAIN n= 2454 gross meanR=+0.032 CI=(-0.017,+0.074)  net2bps=-0.125  win=50.8%
   VALID n= 1072 gross meanR=+0.047 CI=(-0.021,+0.117)  net2bps=-0.131  win=49.1%
   OOS   n= 1435 gross meanR=+0.044 CI=(-0.017,+0.104)  net2bps=-0.138  win=50.8%

[fix pivot tie rule only (TradingView rule)] trades=4959
   TRAIN n= 2452 gross meanR=+0.033 CI=(-0.013,+0.077)  net2bps=-0.124  win=50.9%
   VALID n= 1072 gross meanR=+0.048 CI=(-0.021,+0.117)  net2bps=-0.129  win=49.2%
   OOS   n= 1435 gross meanR=+0.044 CI=(-0.017,+0.104)  net2bps=-0.138  win=50.8%

[fix weekday only] trades=6187
   TRAIN n= 3056 gross meanR=+0.026 CI=(-0.013,+0.069)  net2bps=-0.129  win=50.4%
   VALID n= 1365 gross meanR=+0.032 CI=(-0.026,+0.089)  net2bps=-0.143  win=49.2%
   OOS   n= 1766 gross meanR=+0.036 CI=(-0.017,+0.089)  net2bps=-0.145  win=49.8%

[BOTH FIXED (current engine)] trades=6185
   TRAIN n= 3054 gross meanR=+0.027 CI=(-0.017,+0.068)  net2bps=-0.128  win=50.4%
   VALID n= 1365 gross meanR=+0.033 CI=(-0.025,+0.090)  net2bps=-0.142  win=49.3%
   OOS   n= 1766 gross meanR=+0.036 CI=(-0.017,+0.089)  net2bps=-0.145  win=49.8%
```
Effect of the two defects: +25% trades (Fridays restored), gross +0.027/+0.033/+0.036 R (CIs include 0), net −0.128/−0.142/−0.145 R. Conclusion unchanged: **no edge, net negative**. Re-run of the earlier headline numbers after the fixes (`rerun_post_fix.txt`): literal script, 1m, REALISTIC execution: gross +0.036/+0.049/+0.003 R, net(2 bps) −0.291/−0.356/−0.405 R; every year 2017–2026 net negative.

## 3. Phase 3 — mechanics audit
| check | result | evidence |
|---|---|---|
| Entry timing: signal bar close vs next-bar open | audited; as-plotted keeps close fill, realistic uses next open; a gap beyond the stop at the open cancels the fill (counted: 2 cases in the 16.7k-trade 1m run, 0 in the 5m fixed run) | `test_next_open_fill_and_gap_beyond_stop_is_skipped` |
| Stop/target order when OHLC cannot reveal it | conservative default (stop first). Sensitivity switch `favorable_first`: at 1-minute bars with 4R targets it changes mean R by only +0.0013 (+0.0213 vs +0.0226 on the same 7,304 trades) — ordering ambiguity is rare there; it would matter for 1R targets (not measured) | bias audit, `test_stop_target_ordering_…` |
| Gap through stop / target | realistic: exits at the bar open (worse for stops, better for targets); as-plotted: exits at the level. **The as-plotted treatment truncates adverse overnight gaps at −1R and is the main source of its inflated gross (§5)** | `test_gap_fills_use_the_open_not_the_level`, decomposition |
| Duplicate entries/exits, one position at a time | property test on 12 random sessions × 4 execution modes: signals strictly after the previous exit, one terminal exit per trade, partial weights sum to 1, daily trade cap respected | `test_one_position_at_a_time_…` |
| Session-end exits, overnight | clock rule (no peek at next bar); missing closing bars → flagged `EOD_MISSED` at next open; as-plotted holds 30% of 5m trades overnight (one 232 days in the 1m run) | D3 + tests |
| No-trade periods / missing candles | 0.5% missing 1-minute bars (NIFTY: 4,123 of ~873k); both implementations agree on gapped data | parity, data audit |
| Slippage / spread / commission | cost = 2·(commission + slippage) + spread, `round_trip_cost_bps()`; applied as bps of price converted to R per trade; no partial-fill model | `test_cost_model_components` |
| Instrument mapping | NIFTY/BANKNIFTY/SENSEX = captured INDEX candles (not futures); CRUDEOIL/NATURALGAS = captured FUTURE candles (MCX session 09:00–23:30); explicit session per run | data audit |
| Engine R vs independent tick-level evaluator | **trade-for-trade equal** at full resolution (as-plotted and realistic, PINE exit) | `tests/test_hybrid_mp_bias_world.py` |
| Look-ahead | prefix-invariance (truncated vs full data) for baseline, fixed, SMC, MTF; D3 removed the one next-bar dependency | tests |

### Data audit (`data_quality_audit.txt`)
```

NIFTY INDEX  file=NIFTY_INDEX_2017-01-01_2027-01-01.npz sha256[:16]=678b29a7a1130f78
  bars=931831 sessions=2327 first=2017-06-01 last=2026-10-12 15:29
  bars/session expected 375: median 375, sessions with >=99% bars 99.1%, <90% bars 16 sessions, missing bars total 4123
  duplicate timestamps=0 | bars outside session=63467 | weekend bars (fixed weekday)=3663 | first bar at session open in 91.0% of sampled days
  OHLC inconsistent bars=12 | zero-range bars=6.8% | zero-volume bars=91.7%
  largest overnight jumps (open vs prev close): 2020-03-23 -9.15%, 2021-09-27 -6.75%, 2020-03-13 -5.81%

BANKNIFTY INDEX  file=BANKNIFTY_INDEX_2017-01-01_2027-01-01.npz sha256[:16]=3b3f5bf8b0e4c151
  bars=522262 sessions=1258 first=2021-09-29 last=2026-10-14 15:29
  bars/session expected 375: median 375, sessions with >=99% bars 98.3%, <90% bars 15 sessions, missing bars total 3588
  duplicate timestamps=0 | bars outside session=54900 | weekend bars (fixed weekday)=2691 | first bar at session open in 84.8% of sampled days
  OHLC inconsistent bars=4 | zero-range bars=10.6% | zero-volume bars=85.3%
  largest overnight jumps (open vs prev close): 2026-02-03 +4.79%, 2025-04-07 -4.23%, 2026-04-08 +4.17%

SENSEX INDEX  file=SENSEX_INDEX_2017-01-01_2027-01-01.npz sha256[:16]=348d3548622f8413
  bars=502648 sessions=1258 first=2021-09-29 last=2026-10-14 15:29
  bars/session expected 375: median 375, sessions with >=99% bars 98.1%, <90% bars 14 sessions, missing bars total 4092
  duplicate timestamps=0 | bars outside session=35568 | weekend bars (fixed weekday)=2295 | first bar at session open in 83.8% of sampled days
  OHLC inconsistent bars=68 | zero-range bars=6.9% | zero-volume bars=84.8%
  largest overnight jumps (open vs prev close): 2026-08-01 +6.70%, 2026-08-03 -5.26%, 2025-04-07 -5.25%

CRUDEOIL FUTURE  file=CRUDEOIL_FUTURE_2017-01-01_2027-01-01.npz sha256[:16]=9b5dd3b103f9c267
  bars=57414 sessions=69 first=2026-07-02 last=2026-10-12 20:28
  bars/session expected 870: median 870, sessions with >=99% bars 76.8%, <90% bars 8 sessions, missing bars total 2620
  duplicate timestamps=0 | bars outside session=9 | weekend bars (fixed weekday)=0 | first bar at session open in 78.3% of sampled days
  OHLC inconsistent bars=0 | zero-range bars=50.8% | zero-volume bars=43.2%
  largest overnight jumps (open vs prev close): 2026-07-14 +10.26%, 2026-07-27 -4.19%, 2026-08-03 -4.02%

NATURALGAS FUTURE  file=NATURALGAS_FUTURE_2017-01-01_2027-01-01.npz sha256[:16]=0f7a1fa2816794e6
  bars=62601 sessions=74 first=2026-06-30 last=2026-10-12 20:28
  bars/session expected 870: median 870, sessions with >=99% bars 77.0%, <90% bars 5 sessions, missing bars total 1784
  duplicate timestamps=0 | bars outside session=10 | weekend bars (fixed weekday)=0 | first bar at session open in 73.0% of sampled days
  OHLC inconsistent bars=0 | zero-range bars=53.6% | zero-volume bars=40.3%
  largest overnight jumps (open vs prev close): 2026-07-13 -3.82%, 2026-10-08 +2.55%, 2026-07-27 -2.22%
```

## 4. Phase 4 — random-walk bias
Design (`scripts/hybrid_mp_bias_audit.py`, deterministic seeds, parameters printed in each header): one near-continuous true path (600 Gaussian steps per 1-minute bar, bar σ 3.5 points, overnight gap σ 40) is a martingale, so any stopping rule has true-path expectancy 0. The engine only sees OHLC bars built from a *sample* of that path (600/stride samples per bar). The same trades are then re-evaluated by walking the true path. `bar − true` isolates the effect of building bars from sampled data. (≈60 samples/bar ≈ one value per second.)

```
world: 16 seeds x 40 sessions x 375 bars, true path 600 steps/bar, bar sd 3.5 pts; strides (samples/bar)= [10, 20, 60, 120, 300, 600]

== AS-PLOTTED (close fill, level exits, PINE exit)
samples/bar  trades      bar-level R      true-path R       bar - true
         10    8902  +0.0818±0.0427  +0.0268±0.0419  +0.0550±0.0137
         20    8170  +0.0614±0.0443  +0.0142±0.0436  +0.0471±0.0122
         60    7324  +0.0444±0.0465  +0.0218±0.0461  +0.0225±0.0090
        120    7231  +0.0440±0.0468  +0.0371±0.0467  +0.0069±0.0066
        300    6880  +0.0315±0.0478  +0.0301±0.0477  +0.0015±0.0028
        600    6815  +0.0354±0.0481  +0.0354±0.0481  +0.0000±0.0000

== REALISTIC (next-open, gap fills, EOD flat, PINE exit)
samples/bar  trades      bar-level R      true-path R       bar - true
         10    8453  +0.0619±0.0421  -0.0009±0.0412  +0.0628±0.0136
         20    7913  +0.0316±0.0429  -0.0117±0.0422  +0.0433±0.0114
         60    7304  +0.0213±0.0443  +0.0008±0.0440  +0.0205±0.0079
        120    7112  +0.0200±0.0448  +0.0125±0.0447  +0.0075±0.0062
        300    6923  +0.0109±0.0452  +0.0073±0.0451  +0.0037±0.0032
        600    6862  +0.0028±0.0452  +0.0028±0.0452  +0.0000±0.0000

== FIXED-style (REALISTIC + 25% partials, BE after T1)
samples/bar  trades      bar-level R      true-path R       bar - true
         10   10777  +0.0468±0.0233  +0.0125±0.0227  +0.0343±0.0082
         20   10232  +0.0296±0.0237  +0.0038±0.0232  +0.0259±0.0070
         60    9496  +0.0305±0.0244  +0.0182±0.0242  +0.0123±0.0054
        120    9275  +0.0338±0.0247  +0.0266±0.0245  +0.0072±0.0043
        300    9113  +0.0286±0.0248  +0.0237±0.0246  +0.0049±0.0029
        600    9006  +0.0282±0.0248  +0.0282±0.0248  +0.0000±0.0015

== ordering sensitivity at 60 samples/bar (REALISTIC PINE): adverse-first (default) vs favourable-first
  favorable_first=False trades=7304 bar-level R=+0.0213±0.0443
  favorable_first=True  trades=7304 bar-level R=+0.0226±0.0443
exit=0
```
Longer check of the fixed-style rule, 60 further seeds (seed offset 5000), because its 16-seed true-path column looked slightly positive:
```
world: 60 seeds x 40 sessions x 375 bars, true path 600 steps/bar, bar sd 3.5 pts; strides (samples/bar)= [600, 60]

== FIXED-style (REALISTIC + 25% partials, BE after T1)
samples/bar  trades      bar-level R      true-path R       bar - true
        600   35111  +0.0015±0.0125  +0.0020±0.0125  -0.0005±0.0007
         60   36553  +0.0160±0.0124  +0.0064±0.0123  +0.0096±0.0026
exit=0
```
Findings:
1. `bar − true` is positive, **monotonically decreasing with sampling density, and exactly 0 at full resolution** (e.g. realistic PINE: +0.063 → +0.043 → +0.021 → +0.008 → +0.004 → 0.000). At one-second-like sampling it is ≈ +0.02 R (PINE) / +0.01 R (fixed-style).
2. True-path expectancy is ≈ 0 for the executable (realistic) rules: PINE exit −0.012…+0.013 (± 0.045), **fixed-style +0.0020 ± 0.0125 over 35,111 trades at full resolution**; the 16-seed fixed-style column (+0.004…+0.028 ± 0.023) was noise. The *as-plotted* rule is the exception: its true-path R is +0.014…+0.037 (± 0.048) in all six rows because it books adverse overnight gaps at the stop level — an accounting convention, not a martingale violation (§5).
3. Entry timing, signal confirmation and exit logic therefore create **no** expectancy; the bias is entirely the sampled-OHLC effect (a bar built from sampled values under-states its extremes, so near stops are touched less often than in the continuous process).
4. Real-data sign-flip null of the fixed configuration (NIFTY 5m, 2024-.., 8 seeds, after all fixes): mean ≈ +0.022 R, range −0.043…+0.072; real OOS gross +0.036 R lies inside that range:
```
REAL OOS subset (fixed, 5m):   gross                        n= 1764 win=49.8% meanR=+0.036 CI=(-0.022,+0.089) PF=1.08 bps= +0.85 totR=  +64.4 maxDD_R=39.7
  null seed 0:   gross                        n= 1822 win=51.5% meanR=+0.072 CI=(+0.009,+0.126) PF=1.15 bps= +1.02 totR= +131.5 maxDD_R=24.6
  null seed 1:   gross                        n= 1847 win=50.6% meanR=+0.046 CI=(-0.012,+0.098) PF=1.10 bps= +0.62 totR=  +84.5 maxDD_R=40.0
  null seed 2:   gross                        n= 1822 win=47.3% meanR=-0.043 CI=(-0.093,+0.006) PF=0.91 bps= -0.76 totR=  -78.7 maxDD_R=88.5
  null seed 3:   gross                        n= 1840 win=49.3% meanR=+0.009 CI=(-0.040,+0.061) PF=1.02 bps= +0.09 totR=  +15.8 maxDD_R=53.9
  null seed 4:   gross                        n= 1783 win=48.7% meanR=-0.008 CI=(-0.062,+0.047) PF=0.98 bps= +0.06 totR=  -14.1 maxDD_R=64.2
  null seed 5:   gross                        n= 1863 win=49.7% meanR=+0.002 CI=(-0.043,+0.059) PF=1.00 bps= -0.20 totR=   +4.3 maxDD_R=33.4
  null seed 6:   gross                        n= 1789 win=51.1% meanR=+0.065 CI=(+0.006,+0.132) PF=1.14 bps= +1.10 totR= +116.6 maxDD_R=60.1
  null seed 7:   gross                        n= 1833 win=51.4% meanR=+0.033 CI=(-0.020,+0.080) PF=1.07 bps= +0.45 totR=  +61.1 maxDD_R=41.8
```
Consequence for reading backtests: a gross edge below ≈ +0.03 R on this data is not distinguishable from zero. No claim of "unexplained positive expectancy" remains.

## 5. Why the literal Pine execution looks so much better (`asis_decomposition.txt`)
```

REAL NIFTY 5m, Pine defaults (gross meanR, 95% CI)
  AS-PLOTTED                       TRAIN n= 2169 +0.208(+0.11,+0.29) | VALID n=  996 +0.114(-0.02,+0.24) | OOS n= 1110 +0.151(+0.05,+0.27) | overnight 30%
  + next-open fill only            TRAIN n= 2165 +0.192(+0.10,+0.27) | VALID n=  995 +0.109(-0.03,+0.23) | OOS n= 1101 +0.123(-0.01,+0.23) | overnight 30%
  + gap fills only                 TRAIN n= 2169 +0.203(+0.09,+0.31) | VALID n=  996 +0.118(-0.06,+0.30) | OOS n= 1110 +0.129(-0.01,+0.27) | overnight 30%
  + EOD flat only (no overnight)   TRAIN n= 2929 +0.063(+0.00,+0.13) | VALID n= 1316 +0.021(-0.06,+0.10) | OOS n= 1771 -0.021(-0.09,+0.05) | overnight 0%
  + next-open + EOD flat           TRAIN n= 2929 +0.061(+0.00,+0.12) | VALID n= 1316 +0.021(-0.06,+0.10) | OOS n= 1771 -0.021(-0.09,+0.05) | overnight 0%
  REALISTIC (all three)            TRAIN n= 2929 +0.061(+0.00,+0.12) | VALID n= 1316 +0.021(-0.06,+0.10) | OOS n= 1771 -0.019(-0.09,+0.05) | overnight 0%

SIGN-FLIP NULL seed 0 (2024-01.. subset; destroys drift/autocorrelation, keeps volatility)
  AS-PLOTTED                       n=  965 gross meanR +0.223 (+0.10,+0.36)
  + next-open fill only            n=  958 gross meanR +0.226 (+0.09,+0.36)
  + gap fills only                 n=  965 gross meanR +0.076 (-0.12,+0.26)
  + EOD flat only (no overnight)   n= 1706 gross meanR +0.061 (-0.02,+0.13)
  + next-open + EOD flat           n= 1705 gross meanR +0.063 (-0.02,+0.14)
  REALISTIC (all three)            n= 1705 gross meanR +0.059 (-0.02,+0.13)

SIGN-FLIP NULL seed 1 (2024-01.. subset; destroys drift/autocorrelation, keeps volatility)
  AS-PLOTTED                       n= 1107 gross meanR +0.107 (-0.00,+0.22)
  + next-open fill only            n= 1100 gross meanR +0.077 (-0.05,+0.20)
  + gap fills only                 n= 1107 gross meanR +0.050 (-0.09,+0.19)
  + EOD flat only (no overnight)   n= 1784 gross meanR +0.055 (-0.03,+0.12)
  + next-open + EOD flat           n= 1784 gross meanR +0.055 (-0.04,+0.12)
  REALISTIC (all three)            n= 1784 gross meanR +0.057 (-0.03,+0.12)

SIGN-FLIP NULL seed 2 (2024-01.. subset; destroys drift/autocorrelation, keeps volatility)
  AS-PLOTTED                       n= 1158 gross meanR -0.011 (-0.14,+0.11)
  + next-open fill only            n= 1151 gross meanR -0.020 (-0.12,+0.10)
  + gap fills only                 n= 1158 gross meanR -0.119 (-0.29,+0.03)
  + EOD flat only (no overnight)   n= 1805 gross meanR -0.050 (-0.12,+0.01)
  + next-open + EOD flat           n= 1805 gross meanR -0.050 (-0.12,+0.01)
  REALISTIC (all three)            n= 1805 gross meanR -0.048 (-0.12,+0.01)
```
The as-plotted setup shows +0.15…+0.21 R gross on 5m, but (a) the same setup on sign-flipped data gives +0.22 / +0.11 / −0.01 R (mean +0.11), (b) adding only the end-of-day exit collapses it to +0.06 / +0.02 / −0.02 R, (c) the gap-fill switch alone removes most of the null's inflation (+0.22 → +0.08). The mechanism: overnight holds credit gap-through-stops at the stop level (−1R) while favourable moves are not capped the same way. The as-plotted numbers are an artefact of optimistic accounting, not an edge.

## 6. Phase 5 — baseline vs SMC (identical data, 2 bps; `smc_comparison.txt`)
```
BASELINE (SMC off)                 | TRAIN n= 3054 gross=+0.027(-0.01,+0.07) net=-0.128 win=50.4% dd= 392.3 | VALID n= 1365 gross=+0.033(-0.02,+0.09) net=-0.142 win=49.3% dd= 203.1 | OOS n= 1766 gross=+0.036(-0.02,+0.09) net=-0.145 win=49.8% dd= 259.2
+ only [sweep]                     | TRAIN n=  626 gross=+0.065(-0.03,+0.16) net=-0.092 win=52.7% dd=  64.6 | VALID n=  260 gross=+0.026(-0.12,+0.16) net=-0.157 win=50.0% dd=  46.1 | OOS n=  429 gross=-0.032(-0.13,+0.09) net=-0.213 win=47.1% dd=  97.4
+ only [inducement]                | TRAIN n= 1217 gross=+0.070(+0.00,+0.14) net=-0.092 win=52.0% dd= 112.5 | VALID n=  577 gross=+0.026(-0.07,+0.13) net=-0.161 win=49.2% dd=  96.1 | OOS n=  751 gross=+0.019(-0.06,+0.10) net=-0.177 win=49.7% dd= 143.0
+ only [displacement]              | TRAIN n= 1781 gross=+0.037(-0.01,+0.09) net=-0.092 win=50.5% dd= 163.4 | VALID n=  844 gross=+0.071(-0.00,+0.15) net=-0.073 win=51.5% dd=  72.7 | OOS n= 1060 gross=+0.095(+0.02,+0.17) net=-0.054 win=52.5% dd=  81.1
+ only [fvg_or_ob_touch]           | TRAIN n= 1927 gross=+0.045(-0.01,+0.10) net=-0.116 win=50.8% dd= 224.7 | VALID n=  922 gross=+0.051(-0.03,+0.13) net=-0.133 win=51.5% dd= 135.4 | OOS n= 1095 gross=-0.008(-0.07,+0.06) net=-0.197 win=47.7% dd= 224.2
+ only [discount_premium]          | TRAIN n=   86 gross=+0.171(-0.10,+0.43) net=-0.028 win=57.0% dd=  11.5 | VALID n=   24 gross=-0.302(-0.72,+0.14) net=-0.479 win=37.5% dd=  15.0 | OOS n=   54 gross=+0.302(-0.03,+0.69) net=+0.099 win=57.4% dd=   7.8
+ score >= 2 of 5                  | TRAIN n= 1727 gross=+0.050(-0.01,+0.10) net=-0.097 win=50.8% dd= 173.4 | VALID n=  819 gross=+0.023(-0.05,+0.11) net=-0.144 win=50.2% dd= 120.3 | OOS n= 1043 gross=+0.022(-0.06,+0.09) net=-0.151 win=49.8% dd= 180.0
+ score >= 3 of 5                  | TRAIN n=  579 gross=+0.048(-0.04,+0.14) net=-0.095 win=52.0% dd=  72.6 | VALID n=  282 gross=+0.060(-0.08,+0.18) net=-0.105 win=51.1% dd=  37.4 | OOS n=  383 gross=-0.006(-0.12,+0.11) net=-0.180 win=48.3% dd=  77.0
+ FULL: all 5 components           | TRAIN n=    0 gross=+0.000(+0.00,+0.00) net=+0.000 win= 0.0% dd=   0.0 | VALID n=    0 gross=+0.000(+0.00,+0.00) net=+0.000 win= 0.0% dd=   0.0 | OOS n=    0 gross=+0.000(+0.00,+0.00) net=+0.000 win= 0.0% dd=   0.0

REGIME BREAKDOWN (all periods; gross | net 2 bps; n)
  BASELINE (SMC off)           volatility         LOW vol        n= 1706 gross=+0.036 (-0.022,+0.096) net=-0.191 win=49.2%
  BASELINE (SMC off)           volatility         MID vol        n= 2027 gross=+0.009 (-0.040,+0.060) net=-0.155 win=49.9%
  BASELINE (SMC off)           volatility         HIGH vol       n= 2393 gross=+0.051 (+0.002,+0.101) net=-0.074 win=50.8%
  BASELINE (SMC off)           trade vs 20d trend with-trend     n= 3232 gross=+0.014 (-0.030,+0.056) net=-0.157 win=48.9%
  BASELINE (SMC off)           trade vs 20d trend counter-trend  n= 2894 gross=+0.054 (+0.014,+0.098) net=-0.106 win=51.3%
  + score >= 2 of 5            volatility         LOW vol        n=  995 gross=+0.049 (-0.021,+0.124) net=-0.165 win=50.9%
  + score >= 2 of 5            volatility         MID vol        n= 1180 gross=+0.001 (-0.066,+0.063) net=-0.155 win=48.7%
  + score >= 2 of 5            volatility         HIGH vol       n= 1380 gross=+0.065 (+0.005,+0.123) net=-0.056 win=51.8%
  + score >= 2 of 5            trade vs 20d trend with-trend     n= 1815 gross=+0.024 (-0.035,+0.077) net=-0.140 win=49.8%
  + score >= 2 of 5            trade vs 20d trend counter-trend  n= 1740 gross=+0.055 (-0.000,+0.106) net=-0.098 win=51.3%
  + FULL: all 5 components     volatility         LOW vol        n=0 (too few)
  + FULL: all 5 components     volatility         MID vol        n=0 (too few)
  + FULL: all 5 components     volatility         HIGH vol       n=0 (too few)
  + FULL: all 5 components     trade vs 20d trend with-trend     n=0 (too few)
  + FULL: all 5 components     trade vs 20d trend counter-trend  n=0 (too few)

BY YEAR (net 2 bps meanR): 2017: base -0.24 / smc2 -0.21 | 2018: base -0.07 / smc2 -0.01 | 2019: base -0.12 / smc2 -0.13 | 2020: base -0.09 / smc2 -0.12 | 2021: base -0.16 / smc2 -0.06 | 2022: base -0.12 / smc2 -0.14 | 2023: base -0.16 / smc2 -0.15 | 2024: base -0.20 / smc2 -0.13 | 2025: base -0.13 / smc2 -0.25 | 2026: base -0.09 / smc2 -0.04
```
Reading: no variant has a positive net expectancy with a confidence interval above 0 in any split. `displacement` shows OOS gross +0.095 (CI +0.02…+0.17) but TRAIN +0.037 and VALID +0.071 (CIs include 0) and net −0.054 OOS — 24 variant×split intervals were examined, one excluding 0 is within chance; `discount_premium` (n=24–86) flips sign between splits. Requiring all five components yields **0 trades** (a continuation breakout is never also in "discount"). SMC stays optional and **off by default** (`smc_filter=False`).

## 7. Cost sensitivity (`cost_sensitivity.txt`; mean net R = gross R − cost_bps ÷ risk_bps)
```
Cost sensitivity, NIFTY 5m, mean net R per trade = mean(r_gross - cost_bps/risk_bps)   (cost = 2*(commission+slippage) + spread, bps of price)

[FIXED_primary_5m]  cost_bps:   0       1       2       3       4       6
   TRAIN n= 3054      +0.027  -0.051  -0.128  -0.206  -0.284  -0.439    median risk 14.7 bps
   VALID n= 1365      +0.033  -0.054  -0.142  -0.229  -0.317  -0.492    median risk 12.8 bps
   OOS   n= 1766      +0.036  -0.054  -0.145  -0.235  -0.326  -0.507    median risk 12.5 bps

[PINE_ASIS_5m]  cost_bps:   0       1       2       3       4       6
   TRAIN n= 2169      +0.208  +0.136  +0.063  -0.009  -0.081  -0.226    median risk 16.2 bps
   VALID n=  996      +0.114  +0.030  -0.055  -0.140  -0.225  -0.395    median risk 14.1 bps
   OOS   n= 1110      +0.151  +0.064  -0.023  -0.110  -0.197  -0.371    median risk 13.5 bps

example decomposition: commission 0.3 bps/side + slippage 0.5 bps/side + spread 0.4 bps -> round trip = 2*(0.3+0.5)+0.4 = 2.0 bps
```
Break-even round-trip cost for the fixed primary is ≈ 0.4–0.5 bps (gross +0.03 R × median risk 12–15 bps) (below any realistic cost); the as-plotted figures need unattainable execution (§5).

## 8. Tests, commands, exit codes
**Full regression suite** (`cd backend && ./scripts/run_tests.sh -q`, live `chanakya.db` protected): **3162 passed, 0 failed, exit code 0** (373 s).
Focused (150 passed, exit 0): `tests/test_weekday_regression.py` (7), `test_hybrid_mp.py` (22), `test_hybrid_mp_api.py` (4), `test_hybrid_mp_audit.py` (22: 14 parity cases, Friday coverage, invariants, ordering, cost, Saturday gating), `test_hybrid_mp_bias_world.py` (2), `test_smart_bos.py`, and the platform G1–G7 suites.
New regression tests, one per confirmed defect: D1 `test_weekday_regression.py` (mutation-checked), D2 `test_pivot_tie_rule_…`, D3 `test_eod_flat_uses_the_clock_…`, D4 `test_pattern_filter_flag_is_a_real_boolean_…`, D5 `test_hybrid_dashboard_market_open_by_wall_clock`.
Reproduce: `cd backend` then
`python scripts/hybrid_mp_audit_baseline.py` (§2) · `python scripts/hybrid_mp_parity.py` and `… legacy_weekday` (parity ledgers) · `python scripts/hybrid_mp_data_audit.py` · `python scripts/hybrid_mp_bias_audit.py 16 40` and `… 60 40 5000 600,60 FIXED` (§4) · `python scripts/hybrid_mp_asis_decomposition.py` (§5) · `python scripts/hybrid_mp_smc_audit.py` (§6) · `python scripts/hybrid_mp_trade_ledger.py` (`TRADE_LEDGER.csv`: 10,460 trades, FIXED primary + as-plotted, with timestamps, SL, T1–T4, R, costs) · `python scripts/hybrid_mp_research.py primary|fixed|fixed_null NIFTY`.

## 9. Remaining blockers / pending
1. **Deploy** the platform candle-engine weekday fix (restart needed; Friday 2026-10-16 is the first affected day).
2. **External parity** needs the TradingView chart-data export described in the parity report §7.
3. ~~`smart_bos` re-run~~ **done**: NO-GO re-confirmed after the weekday fix (`smart_bos_rerun_after_weekday_fix.txt`; Pine baseline gross +0.014…+0.044 R, frozen Renko OOS unchanged at +0.149 R gross / +0.049 R net 2 bps).
4. Dashboard API history loader (`load_1m`) writes a `.npz` cache file into `data/research/smart_bos/cache/` when a cache file is missing (derived data only, but the earlier "writes nothing" claim is not strictly true); the dashboard service was not modified in this audit.
5. No measured transaction costs, no option-premium/lot/margin model, no NSE-futures or equity data, MCX futures series possibly roll-contaminated.
