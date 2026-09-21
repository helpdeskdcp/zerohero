# TA/Options-PDF Upgrade Candidates — Verdict

Source: Fidelity "Technical Analysis for Options Trading" webinar deck (35 slides)
+ Zerodha Varsity Module 5 "Options Theory for Professional Trading" (245 pages),
compared against this codebase. Almost everything both documents describe is
already implemented here, usually more rigorously (real Black-Scholes Greeks in
`app/greeks_engine/` vs the PDF's approximation table; real OI-based S/R walls
in `sr_engine` vs textbook peak/trough lines; real skew/IV-vs-RV computation in
`app/optionchain/structure.py` vs a narrative chart). Two genuine gaps were
found and backtested here. **`app/engines/scalp_strategy.py` was NOT modified
by this research — confirmed via `git diff`, clean.**

Method: reused the canonical backtest engine's own internals
(`ReplayHarness`, `decide_from_context`, `calibration.fit`) unmodified — same
TRAIN→calibrate→TEST flow as `app.backtest.runner.run_backtest()` — via
`backtest_candidates.py` in this directory. Both candidates are POST-HOC
filters applied to the engine's own real TEST trades (never fed back into the
engine's decision logic). Real 2026-07-13→2026-08-28 data
(`/root/oi_dashboard/oi_history.db`, 35 trading days). Train/test-swap
robustness check run for every symbol (this session's established guard
against the kind of overfitting artifact found earlier in a NIFTY signal
search — an apparent edge that vanished entirely when train/test were
reversed).

## Candidate 1: Bollinger Bands breakout confirmation — **PROMISING, not GO**

Standard formula (SMA(20) ± 2×stddev(20) on 5m underlying closes, Fidelity
deck's own convention). Filter: keep a trade only if, at entry, the
underlying price was already outside the band in the trade's direction
(CE kept if price > upper band, PE kept if price < lower band).

| Run | n (baseline→filtered) | Win rate | Expectancy (pts) | Profit factor |
|---|---|---|---|---|
| NIFTY forward (train Jul13–Aug3, test Aug4–28) | 124 → 18 | 0.355 → **0.667** | 0.687 → **5.011** | 1.309 → **5.579** |
| NIFTY swapped (train Aug4–28, test Jul13–Aug3) | 62 → 16 | 0.323 → **0.562** | 0.041 → **4.050** | 1.013 → **2.813** |
| NATURALGAS swapped (only valid NatGas run, see below) | 66 → 18 | 0.409 → **0.611** | 0.008 → 0.108 | 1.070 → 7.500 |

**Robustness**: the improvement direction survived the train/test swap on
NIFTY (both halves show win-rate roughly doubling and PF improving several
times over) — this is NOT the same failure pattern as the earlier NIFTY
overfitting incident this session, where the edge disappeared entirely on
swap. That is a genuine positive signal.

**Why this is PROMISING and not a GO:**
- Sample size is small after filtering (n=16–18) — this session's own track
  record (calibration work needed n=122 just to detect miscalibration) means
  n≈18 is not enough to rule out a lucky subsample, even with a consistent
  swap direction.
- NIFTY's cost model is `est_cost_r=0` by design in this engine (tight
  weeklies, no calibrated cost — matches every other NIFTY backtest this
  session, not a special favorable treatment for this candidate) — so these
  PFs are pre-real-cost for NIFTY specifically. The *relative* improvement
  (baseline vs filtered, same zero-cost assumption applied to both) is still
  meaningful, but the *absolute* PF numbers are optimistic.
- Not yet checked for a simpler confound: a breakout-outside-the-band filter
  may just be selecting larger-momentum moves in general, which could look
  identical to "the Bollinger Band mattered specifically" without actually
  being distinguishable from, e.g., a plain N-bar-momentum filter. Not ruled
  out here — flagged as the next thing to test before calling this GO.

**If this is pursued further**: do NOT wire into `scalp_strategy.py` yet.
Next step would be (a) a larger sample via more history if/when available,
(b) a momentum-confound control test, (c) real NIFTY cost estimation before
trusting the absolute PF. Only after that would a specific additive
`config["filters"]`-style gate (matching the existing `block_regimes` /
`block_signal_types` pattern already in `scalp_strategy.py`, e.g. a new
`require_bollinger_breakout` filter) be a reasonable, still-optional,
default-OFF proposal.

## Candidate 2: IV-vs-Realized-Vol "cheap premium" entry gate — **NO-GO**

Threshold (median ATM IV / 20-bar realized vol ratio) fit on TRAIN only,
frozen before TEST — filter: keep only entries where IV/RV ratio was at or
below that TRAIN-derived median (i.e., "don't buy when premium is expensive
relative to its own recent realized volatility").

| Run | n (baseline→filtered) | Win rate | Expectancy (pts) | Profit factor |
|---|---|---|---|---|
| NIFTY forward | 124 → 67 | 0.355 → 0.343 | 0.687 → 0.540 | 1.309 → 1.232 |
| NIFTY swapped | 62 → 6 | 0.323 → 0.333 | 0.041 → 0.125 | 1.013 → 1.038 |

No meaningful improvement in either direction — filtering to "cheap IV" trades
performs essentially the same as (or slightly worse than) the unfiltered
baseline. **Clear NO-GO** — the hypothesis that entry-timing on relative IV
cheapness improves this engine's expectancy is not supported by real data.

## NATURALGAS — inconclusive (data issue, not a valid test)

The forward run (TRAIN Jul13–Aug3, TEST Aug4–26) produced **zero closed
trades in TRAIN**, so calibration never fit and the TEST run is meaningless
(reported as `n=0` across the board — not a real NO-GO, just an invalid run).
The swapped run worked for the Bollinger check but IV/RV data was entirely
absent (`threshold: None`) — MCX option-chain IV/greeks population in this DB
is evidently sparser than NSE/BSE index options, consistent with prior
findings this session about MCX data density. NATURALGAS/CRUDEOIL were not
meaningfully testable for either candidate with the current data — would need
a wider date range or a data-density check before retrying.

## Summary

| Candidate | Verdict |
|---|---|
| Bollinger Bands breakout confirmation | **PROMISING** — real, swap-robust improvement, but n too small and one confound (momentum) unchecked to call GO |
| IV-vs-Realized-Vol entry gate | **NO-GO** — no edge in either train/test direction |
| Both, on NATURALGAS/CRUDEOIL | **INCONCLUSIVE** — data too thin to test |

`scalp_strategy.py` and every other live/paper-trading path: **untouched**.
`LIVE_TRADING`/paper mode: not read or modified by this research.
