# HCS calibration report

Auto-generated: `venv/bin/python -c "from app.hcs import calibrate; ..."` (or via /api/hcs/calibration-report).
Source: resolved AUTOSCALP `scalp_signals` outcomes. Uses the EXISTING `app/backtest/calibration.fit()` + `reliability_curve` -- nothing new is trained.

**Re-run 2026-09-29** (post K8 fix, commit 04136c7, 2026-09-21 -- see
`CALIBRATION_K8_POSTFIX_VALIDATION_2026-09-29.md`). Original report below is
superseded; kept as the "before" reference.

**Verdict:** NOT VALIDATED -- INSUFFICIENT. Outcome log is 17 session(s)
(2026-08-31..2026-09-29), effectively one volatility regime, and the
chronological holdout is a few days -- not a walk-forward. The global
logistic curve fits (n>=40) but per-regime / per-tod curves mostly do not.
HCS therefore runs SHADOW and its A+ gate is intentionally strict. Same
conclusion as before -- what changed is the curve itself and the
holdout quality, not the SHADOW status.

## Sample

- resolved WIN/LOSS: **167** (W 84 / L 83, base win-rate **0.503**) -- up from 80
- sessions: 17 (2026-08-31 .. 2026-09-29) -- up from 6

## Global logistic curve (p = sigmoid(k*(s-0.5) + b))

- k = **0.0044**, b = 0.0162, n = 167, fitted win-rate = 0.503, spread = 5
- Before the fix this was k=1.0 (the old hard floor, not an OLS output). Now
  essentially flat -- the honest answer given this system's real, weak edge.
  `x_lo=0.4, x_hi=0.8`: `predict()` will not extrapolate outside this range.

## Per-slice coverage (does a dedicated curve fit?)

**Regime**
- `TRENDING_DOWN` : n=97 -> FITS (was n=38 insufficient)
- `TRENDING_UP` : n=32 -> INSUFFICIENT (<40)
- `RANGE` : n=33 -> INSUFFICIENT (<40)
- `BREAKOUT_REGIME` / `REVERSAL_REGIME` / `HIGH_VOLATILITY` : n<=2 -> INSUFFICIENT

**Signal type**
- `SUPPORT_BREAKDOWN` : n=127 -> FITS (k=0.0, flat)
- `SUPPORT_REVERSAL` : n=27 -> INSUFFICIENT
- `RESISTANCE_REVERSAL` : n=13 -> INSUFFICIENT

**Time-of-day**
- `MORNING` : n=50 -> FITS
- `CLOSE` : n=55 -> FITS
- `OPEN` : n=24, `MIDDAY` : n=38 -> INSUFFICIENT

Every curve that DOES fit now has `k=0.0` (flat) -- correctly reporting "this
score doesn't predict outcome within this slice" instead of an inflated
slope.

## Chronological hold-out reliability

- fit on first 116 rows, scored last 51 (was 56/24)
- **Brier = 0.252** (was 0.232 -- comparable, thin-sample noise)
- **ECE = 0.066** (was 0.1193 -- roughly halved)

| predicted-prob bin | n | predicted | actual |
|---|--:|--:|--:|
| 0.3-0.4 | 15 | 0.395 | 0.533 |
| 0.4-0.5 | 6 | 0.427 | 0.333 |
| 0.5-0.6 | 30 | 0.509 | 0.533 |

No bin above 0.6 at all -- the flat curve can no longer manufacture a
confident prediction the data doesn't support. This is the same pattern
found in the AutoScalp-side post-fix validation (13 post-fix resolved
trades, no predictions above 0.6).

## What this means for the HCS engine

- The score->probability curve **fits globally and is now honestly flat**
  rather than confidently (and wrongly) sloped.
- Still **no per-regime / per-tod calibration** for most slices and **no
  real walk-forward** (17 sessions, still effectively one regime) -- HCS
  stays SHADOW, A+ gate stays strict. This has not changed and should not
  change until the coverage table above fills in with more regimes.
- Re-run this report as the outcome log grows; promote nothing until it
  spans >= 40 resolved signals per regime across >= 2 regimes with a genuine
  chronological hold-out.
