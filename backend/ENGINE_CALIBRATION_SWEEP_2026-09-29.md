# Engine Calibration Sweep -- 2026-09-29

User request: "backtest karun engines calibration kara sagle" -- go through
every engine, re-backtest, and fix probability calibration wherever it's
broken. This is the consolidated result across all engines checked.

## 1. AutoScalp -- REAL BUG FOUND AND FIXED

The K8 calibration-overconfidence fix (commit `04136c7`, 2026-09-21) was
already live in code but its live-production effect had never been
independently re-verified. Found it IS working correctly (post-fix
predictions cluster near the honest base rate, no confident predictions
above 0.6 vs the pre-fix inversion), but found and fixed a SEPARATE real bug
along the way: `calib_version` was being silently dropped on every live
signal insert (`app/autoscalp/runner.py`), making it impossible to tell
which model version scored which trade without a manual timestamp split.
Fixed, tested (111 tests pass), deployed. Full detail:
`CALIBRATION_K8_POSTFIX_VALIDATION_2026-09-29.md`. Commit `41ef504`.

## 2. HCS -- validated, benefits from the same fix

Same underlying `app/backtest/calibration.py`. Re-ran the official report:
ECE improved 0.119 -> 0.066, ~doubled sample (80 -> 167 resolved), still
correctly stays SHADOW (per-regime coverage still thin). No bug found here;
report refreshed. `HCS_CALIBRATION_REPORT.md`, commit `681c3f3`.

## 3. mathematical_confluence -- no data to calibrate against

Its "confidence" field is a deterministic composite (`int(min(95, cscore *
0.9))`), not a fitted probability -- there is nothing to Brier/ECE-calibrate
directly. Its only real-world validation path is through Smart Index
Scalper's `smart_scalper_signals` table, which has **zero rows** in the
live DB -- this engine isn't accumulating any outcome history right now.
Nothing actionable; not a bug, just no data flowing.

## 4. orderflow (h1h7_state) -- already self-disclosed, no numeric probability

Categorical state classifier, not a probability model. Every state already
carries a hardcoded `research_status` tag (`SUPPORTED` / `NOT_VALIDATED` /
`RESEARCH_ONLY_PROMISING` / `NO_ESTABLISHED_EDGE`) set directly from this
session's own real backtest findings (H7-trap states = SUPPORTED/AVOID,
most others = NO_ESTABLISHED_EDGE). This is already the honest disclosure a
calibration effort would aim to produce -- nothing to fix.

## 5. institutional_edge -- correctly tiered, no live data

Cost model already has three honest tiers (`OK` real-observed > 
`FORMULA_ESTIMATE` real-formula-but-unobserved > `UNCALIBRATED`). NIFTY/
BANKNIFTY sit at `FORMULA_ESTIMATE` by design -- upgrading to `OK` would
need real captured brokerage contract notes, which don't exist for a
PAPER-only system (structural limit, not a bug). `tracked_edges()` returns
empty -- zero live conditional-edge data has accumulated to evaluate.

## 6. structural_break -- working correctly

Reuses AutoScalp's resolved-row data directly, so it benefits from the K8
fix automatically. Ran `evaluate_now("NIFTY")` fresh: state `NORMAL`, break
score 0/16, no anomalous drift flagged. Working as designed; no bug found.

## 7. liquidity_sweep -- already rigorously tested, frozen by design (not fixable via calibration)

Has its own dedicated `probability.py` + Stage-1 backtest with its own
`fit_index_calibration`. Already backtested on 2yr real NIFTY 5m with a
clean look-ahead audit; frozen NOT READY because the underlying
setup-scoring shows **zero discriminative power** (coin-flip accuracy,
WIN/LOSS setup-scores identical) -- a feature/setup-construction problem,
not a calibration-curve problem. More data volume alone would not change
this verdict; closing this gap needs new feature work, out of scope for a
calibration pass.

## 8. Remaining minor engines

- `optionchain/qualify.py`: consumes another engine's `calibrated_probability`
  directly -- not an independent model, nothing to calibrate on its own.
- `sl_hit_analysis`: its `probability_vs_stop_rate()` diagnostic (n=274,
  broader pool than AutoScalp's LIVE-only filter) shows the same
  legacy-data-dominated inversion pattern already explained in item 1 --
  correctly reporting on the same underlying (still mostly pre-fix) data,
  not a bug in this module itself.
- `smart_index_scalper`: replay/backtest framework exists
  (`replay.py`/`replay_metrics.py`, own `_cal_verdict`/`INSUFFICIENT_SAMPLE`
  logic) but has no CLI or API entry point -- only invoked from its test
  harness. Its `INSUFFICIENT_SAMPLE` status is a documented real-data-window
  limitation (same category as liquidity_sweep), not a model defect.

## Bottom line

One real, concrete bug found and fixed (AutoScalp's `calib_version`
drop), one already-fixed bug independently re-verified as genuinely working
in production (K8), and one report refreshed (HCS). Every other engine
checked either (a) has no probability output to calibrate, (b) has zero
live outcome data accumulated to calibrate against, or (c) is already
correctly, honestly disclosing its own validation status. None of these are
bugs to fix -- they're structural realities of a system that is
(correctly) conservative about claiming calibration it hasn't earned.
