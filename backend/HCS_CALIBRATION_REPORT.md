# HCS calibration report

Auto-generated: `venv/bin/python -c "from app.hcs import calibrate; ..."` (or via /api/hcs/calibration-report).
Source: resolved AUTOSCALP `scalp_signals` outcomes. Uses the EXISTING `app/backtest/calibration.fit()` + `reliability_curve` -- nothing new is trained.

**Verdict:** NOT VALIDATED -- INSUFFICIENT. Outcome log is 6 session(s) (2026-08-31..2026-09-07), effectively one volatility regime, and the chronological holdout is a few days -- not a walk-forward. The global logistic curve fits (n>=40) but per-regime / per-tod curves mostly do not. HCS therefore runs SHADOW and its A+ gate is intentionally strict.

## Sample

- resolved WIN/LOSS: **80**  (W 44 / L 36, base win-rate **0.55**)
- sessions: 6  (2026-08-31 .. 2026-09-07)
- min rows for a fitted curve: 40

## Global logistic curve  (p = sigmoid(k*(s-0.5) + b))

- k = 1.0 , b = 0.1877 , n = 80 , fitted win-rate = 0.55 , score-spread buckets = 5

## Per-slice coverage (does a dedicated curve fit?)

**Regime**
- `TRENDING_DOWN` : n=38  -> INSUFFICIENT (<40)
- `TRENDING_UP` : n=20  -> INSUFFICIENT (<40)
- `RANGE` : n=19  -> INSUFFICIENT (<40)
- `BREAKOUT_REGIME` : n=1  -> INSUFFICIENT (<40)
- `REVERSAL_REGIME` : n=2  -> INSUFFICIENT (<40)

**Signal type**
- `SUPPORT_BREAKDOWN` : n=42  -> FITS
- `SUPPORT_REVERSAL` : n=25  -> INSUFFICIENT (<40)
- `RESISTANCE_REVERSAL` : n=13  -> INSUFFICIENT (<40)

**Time-of-day**
- `OPEN` : n=6  -> INSUFFICIENT (<40)
- `MORNING` : n=17  -> INSUFFICIENT (<40)
- `MIDDAY` : n=24  -> INSUFFICIENT (<40)
- `CLOSE` : n=33  -> INSUFFICIENT (<40)

Fitted curves emitted: `*|SUPPORT_BREAKDOWN`

## Chronological hold-out reliability

- fit on first 56 rows, scored last 24
- **Brier = 0.232** , **ECE = 0.1193**  (lower is better; 6-day sample -> noisy)

| predicted-prob bin | n | predicted | actual |
|---|--:|--:|--:|
| 0.5-0.6 | 22 | 0.5619 | 0.6818 |
| 0.6-0.7 | 2 | 0.613 | 0.5 |

## What this means for the HCS engine

- The score->probability curve **fits globally** but there is **no per-regime / per-tod calibration** and **no walk-forward** (6 sessions, one regime).
- HCS therefore runs **SHADOW** and its A+ gate is deliberately strict (HCS >= 68, calibrated p >= 0.56, confidence in {HIGH, MEDIUM}, zero hard vetoes).
- Re-run this report as the outcome log grows; promote nothing until it spans >= 40 resolved signals per regime across >= 2 regimes with a genuine chronological hold-out.
