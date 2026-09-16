# NATGAS Futures Walk-Forward Backtest — Report

## Scope and honest limitation

This backtests the **raw directional signal** (`app.engines.sr_engine.compute_sr`
+ `app.engines.state_classifier.classify` — the exact functions
`decide_from_context` calls before any option-chain-dependent stage) against
**real NATGAS MCX futures 1-minute data**, entirely in underlying-futures
points.

**It does not reproduce NATGAS's actual option-premium P&L.** In production,
`decide_from_context`'s target/SL/trailing (`_plan_from_leg`) are sized off
the *selected option leg's own ATR* (via `analyse_leg` on that leg's own
candles), not the underlying's ATR — and no multi-month real NATGAS
option-premium history exists anywhere in this system (only ~10 real days via
the live histcap capture). Building a true premium-level walk-forward would
need either months more live capture, or a synthetic BS + assumed-IV pricing
layer, which this backtest deliberately does not fabricate. This is the same
mechanism-vs-performance distinction already applied to every other backtest
this session (MTF Cascade, Strategy Verification Engine).

## Data

Real Upstox v3 1-minute OHLCV+OI for `NATURALGAS FUT 25 SEP 26`
(`data/historical/upstox_v3_validation/raw_futures_windows_NATURALGAS_FUT_25_SEP_26_2026-09-10.json.gz`).
Usable range **2026-05-20 → 2026-09-10** (~3.75 months, 64,230 real 1-minute
candles; two earlier windows came back empty from the API and were dropped).

## Method

- Real 1m candles replayed tick-by-tick through `app.autoscalp.aggregator.CandleAggregator`
  (the same aggregator the live runner uses) to build causally-correct 5m/15m/30m bars.
- At every newly-closed 5m bar, `compute_sr(bars_by_tf, chain=None, mode="index")`
  + `classify(bars_by_tf, sr, chain=None)` are called with only data available
  up to that bar — no look-ahead in signal generation.
- Production's own filters (`block_regimes=["UNSTABLE"]`, `block_signal_types=["RESISTANCE_BREAKOUT"]`,
  `block_tod=["AFTERNOON"]`) are applied to separate "raw state" from
  "would actually reach production's later gates."
- Outcome simulation reuses **real subsequent bars** (this is the outcome
  step, not signal generation — using real future bars here is correct, same
  as every prior backtest this session) and mirrors
  `app/engines/paper_trading.py`'s exact trailing/exit formula (profit-lock
  ladder at 0.6R/1.0R, flat-distance trail ratchet, hit_sl/hit_t1, hard
  time-stop), applied to underlying points with NATGAS's actual production
  multiples: `sl_atr=1.1, t1_atr=1.7, t2_atr=2.6` (global defaults — NATGAS's
  own profile does not override these) and `trail_atr=1.6, max_hold_sec=1800`
  (NATGAS's actual overrides, from `app/autoscalp/runner.py:240-242`).

## Results

**N = 7,807 raw states** (every 5m bar with a non-NONE classification);
**3,527 pass production's regime/signal-type/tod filters** (55% blocked,
mostly `UNSTABLE` regime).

| | ALL raw states (n=7807) | Filter-passing (n=3527) |
|---|---|---|
| MFE (×R) | median 0.64, p75 1.06, p90 1.63 | median 0.62, p75 1.03, p90 1.59 |
| MAE (×R) | median 0.67, p75 1.22 | median 0.66, p75 1.24 |
| T1 touch rate | 11.6% | 10.6% |
| T2 touch rate | 3.9% | 3.6% |
| Exit mix | TRAIL 41%, STOP 37%, TARGET 12%, TIME 10% | TRAIL 41%, STOP 38%, TARGET 11%, TIME 11% |
| Win rate / expectancy / PF | 54.9% / −0.047 / 0.761 | 54.3% / −0.055 / 0.733 |

**This closely matches the live 54-signal sample's own numbers** (T1 touch
9.3%, MFE median 0.56R, p90 1.59R) — strong convergent evidence that NATGAS's
p90 realized move genuinely sits around 1.6R, not a fluke of a thin live
sample.

### Target-distance sensitivity (path-correct re-simulation, not MFE-approximated)

| t1_atr | T1 touch % | Win % | Expectancy | PF |
|---|---|---|---|---|
| 0.6 | 54.6% | 56.1% | −0.033 | 0.834 |
| 0.8 | 42.2% | 54.3% | −0.026 | **0.873 (best tested)** |
| 1.0 | 31.4% | 54.3% | −0.028 | 0.866 |
| 1.2 | 22.5% | 54.3% | −0.039 | 0.811 |
| 1.4 | 16.2% | 54.3% | −0.045 | 0.784 |
| **1.7 (current)** | 10.6% | 54.3% | −0.055 | 0.733 |

Shortening `t1_atr` monotonically improves PF and touch rate — but **PF stays
below 1.0 at every tested distance**, including the best one. The raw
directional signal, evaluated purely on the underlying with no option-quality
or EV filtering, does not show positive expectancy at any target distance in
this reconstruction. This means production's option-side gates (quality
score, EV threshold, confidence, CE/PE confirmation — all excluded here since
`chain=None`) are doing real, necessary work; `t1_atr` alone is not a
sufficient lever to make the underlying-level signal profitable.

## Conclusion

1. **T1 is set too far** — confirmed at much higher confidence (7,807 / 3,527
   samples vs. 54 live) than the earlier live-only diagnostic. p90 MFE is
   ~1.6R; `t1_atr=1.7` is essentially unreachable for all but the rare
   outlier move.
2. **Shortening it is not, by itself, a fix** — even the best tested distance
   (0.8×ATR) keeps PF under 1 in this underlying-only reconstruction. Any
   real recommendation needs the option-quality/EV/confidence gates back in
   the loop, which this backtest cannot do without real historical NATGAS
   option premiums.
3. No parameter change was made. Per instruction, this is diagnostic evidence
   for a human engineering decision, not an auto-applied tuning pass.

## Reusable artifacts

- `scripts/natgas_futures_backtest.py` — walk-forward harness (rerunnable, config constants at the top)
- `scripts/natgas_target_sensitivity.py` — path-correct target-distance sweep
- `data/research/natgas_futures_backtest/` — resolved rows, 5m bar series, raw signal log (real data, reusable for a future option-premium-aware version if a synthetic pricing layer is ever built)
