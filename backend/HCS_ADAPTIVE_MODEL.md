# HCS adaptive model — dependency-free online sigmoid unit

**SHADOW / ADVISORY.** `app/hcs/adaptive.py`. Pure Python, zero new dependency,
deterministic. Trains on the same resolved AUTOSCALP outcomes as the calibration
report, **adapts on every new resolved outcome**, and is shown **side-by-side**
against the existing closed-form logistic (`app/backtest/calibration.py`).

**It does NOT gate trades** — the HCS A+ gate still uses `calibrated_probability`.
Nothing frozen is touched; `live_trading` stays `false`.

## Why a 1-unit model, not a hidden-layer NN

80 resolved WIN/LOSS outcomes over **6 sessions / one regime** (see
`HCS_CALIBRATION_REPORT.md`). A single **L2-regularised sigmoid unit with a
base-rate bias** = online logistic regression — a shrinkage estimator that is
appropriate for that volume. A multi-layer net (or torch) would overfit and adds
~GBs of dependency + non-determinism to a deliberately lean, deterministic
codebase. Revisit a real NN only once ≥ 40 outcomes per regime across ≥ 2
regimes exist.

## Model

- `z = b + Σ wₖ·xₖ` , `p = sigmoid(z)`. SGD on log-loss: `wₖ -= lr·(p−y)·xₖ + L2`.
- init: `w = 0`, `b = logit(base win-rate)` → a fresh model predicts the base
  rate for everything (a sane prior).
- `_EPOCHS = 40` deterministic passes over the **chronological** stream, gentle
  LR decay. Same rows in → identical weights out (unit-tested).
- Features (bounded, ~24): `signal_score`, `state_score`, `mtf`, `tanh(momentum)`,
  `rr`, `ev_r`, `hcs_score`, `evidence_coverage`, `setup_memory`, one-hots for
  regime / signal_type / time-of-day.
- State persisted to `data/hcs_adaptive_model.json` (git-ignored). Re-fit on
  demand via `GET /api/hcs/adaptive` or when new outcomes appear.

## Result (2026-09-07 — 80 outcomes, 6 sessions, one regime)

Walk-forward: fit on first 56 (chronological), score last 24.

| model | Brier ↓ | ECE ↓ |
|---|--:|--:|
| adaptive online-logit | 0.2432 | 0.232 |
| **existing closed-form logistic** | **0.2320** | **0.1194** |
| winner | **existing_logistic** | |

Top adaptive weights: `reg_TRENDING_UP −1.68`, `momentum +0.73`, `ev_r +0.72`,
`typ_SUPPORT_REVERSAL +0.66`, `typ_RESISTANCE_REVERSAL −0.63`, `rr +0.60`,
`tod_CLOSE −0.52`, `tod_MIDDAY +0.44` — these are picking up **one-week
idiosyncrasies** (TRENDING_UP setups happened to lose that week), i.e. the model
is fitting sample noise. That is exactly why it stays advisory.

## Honest verdict

**The adaptive model does NOT beat the existing calibration on the only data
that exists** (higher Brier + ECE on the hold-out). Expected — the closed-form
logistic is lower-variance on 56 training rows. Installed, wired, deterministic,
SHADOW-only. `NOT VALIDATED`, nothing `PROVEN`.

The value is the plumbing: as the outcome log grows across sessions and regimes,
re-hit `/api/hcs/adaptive` and watch whether the adaptive model's hold-out Brier
overtakes the logistic. Promote it into the A+ gate only after it wins the
walk-forward across ≥ 2 regimes with a genuine untouched hold-out.

## Surfaced

- `GET /api/hcs/adaptive` — full report (weights, walk-forward comparison).
- `GET /api/hcs/evaluate` — each result now carries `adaptive_probability`,
  `adaptive_status`, `adaptive_vs_calibrated` (delta) next to
  `calibrated_probability`.
- Research → HCS Engine panel: an "Adaptive model" line (per-symbol
  adaptive p + the hold-out winner).
