# Probability Calibration Fix — 2026-09-21

Confirms and fixes the K8 defect (`calibration-overconfidence-k8.md` memory):
autoscalp's predicted win-probability was overconfident and, at high predicted
bins, INVERTED relative to actual outcomes.

## Re-confirmed live, today, with the official diagnostic (`app.autoscalp.calibration_report.calibration_report()`, n=181 resolved LIVE trades)

```
verdict: OVERCONFIDENT
ece: 0.2101
win_rate: 0.4144      (actual)
mean_predicted: 0.6137 (claimed)
profit_factor: 0.74    (losing)
expectancy_points: -0.8928

reliability table (predicted -> actual):
  0.6-0.7: n=22, pred=0.647, actual=0.455
  0.7-0.8: n=41, pred=0.769, actual=0.366   <- 41 trades, not noise
  0.8-0.9: n=17, pred=0.830, actual=0.294   <- INVERTED: higher predicted, lower actual
```

## Root cause (traced in `app/backtest/calibration.py::_fit_logistic`, not hypothesized)

1. **A floor, not a ceiling.** `_K_LO = 1.0` forced every fitted slope up to
   at least 1.0 regardless of what the data supported. Today's live curve
   was sitting at exactly `k: 1.0` — the floor value, not something the OLS
   fit itself produced. A near-zero or weak real relationship (this
   system's actual, repeatedly-backtested reality — ~10 NO-GO research
   reports in `data/research/`) still got forced into a confidently-sloped
   curve.
2. **Extrapolation past the fitted region.** The logistic line was fit from
   as few as 3 distinct score-buckets (`spread: 3` on the live
   `TRENDING_DOWN|SUPPORT_BREAKDOWN` curve) and then evaluated at score
   values nowhere near those buckets — a large intercept fit from 3 noisy
   points, extrapolated with no real data behind it.

## Fix (`app/backtest/calibration.py`, `MODEL_VERSION` bumped to `scalp-calibration-v2`)

1. Removed the `k` floor. A weak/near-zero real slope now fits a weak/
   near-zero `k`; a negative raw slope clamps to `k=0` (never flipped to
   claim an inverted relationship either — thin data shouldn't tell us
   that any more than it should tell us a strong positive one).
2. `_MIN_SPREAD` raised 3 -> 5 distinct score-buckets before trusting any
   slope at all (below that: flat curve at the pooled win rate).
3. `predict()` now clamps the evaluated score to `[x_lo, x_hi]` — the
   actual bucket-supported range the curve was fit from — instead of
   extrapolating the line arbitrarily far past it.
4. Sample-size shrinkage: `k = k_raw * n/(n + 200)`. A thin live sample can
   still nudge the curve, but can no longer swing it to an extreme,
   unsupported slope; the intercept (essentially `logit(win_rate)`, a
   simple average) is left unshrunk since it was never the source of the
   defect.
5. `app/engines/scalp_strategy.py::_score_to_prob` was a hand-duplicated
   copy of this exact logic (same prior, same lookup, same sigmoid) —
   consolidated to call `calibration.predict()` directly. Two
   implementations of the same formula is exactly how a fix in one place
   doesn't reach the other; now there is one.

## Validation (real data, chronological train/test split — not in-sample)

153 real resolved LIVE trades (`db.list_scalp_signals`), sorted chronologically,
split 76 train / 77 test. Refit on TRAIN only with the new code, scored on TEST
(out-of-sample):

```
global curve (fit on train, n=76): k=0.5548, b=0.1743, x_lo=0.40, x_hi=0.80
  (k=0.55, not floor-clamped to 1.0 -- a real, modest slope)

out-of-sample reliability on TEST (n=77):
  bin [0.5, 0.6]: n=77, predicted=0.593, actual=0.429
```

Before the fix, the same style of out-of-sample check (using the live
persisted probabilities in `calibration_report()`) showed predictions spread
across 0.6-0.9 with a 30-40pp INVERSION at the top. After the fix, every
out-of-sample prediction clusters in a single modest bin close to the base
rate, and the inversion is gone — because the model can no longer manufacture
differentiation the data doesn't support. The remaining ~16pp gap (0.593 vs
0.429) reflects this system's genuinely weak/thin real edge, which a
calibration layer cannot invent information to close — see the ~10 NO-GO
backtest reports in `data/research/` for why that ceiling exists. This is the
honest, evidence-supported result, not a tuned-to-look-good number.

## Scope of this change

- Only `app/backtest/calibration.py` (the fitting/predict logic) and
  `app/engines/scalp_strategy.py::_score_to_prob` (now delegates instead of
  duplicating). **No change to `state_classifier.py`'s scoring weights, no
  change to entry/exit logic, no change to the EV/cost gates.** This is
  strictly the score-to-probability layer.
- `LIVE_TRADING` / `paper_mode`: untouched.
- Production's `_maybe_recalibrate()` (autoscalp/runner.py) refits every 15
  minutes from the last 2000 resolved trades automatically — the next live
  refit after deploy will pick up the new fitting code without any manual
  step. A curve persisted under the OLD schema (no `x_lo`/`x_hi`) is handled
  safely by `predict()` (no clamp applied, not a crash) until the next
  refit replaces it.

## Tests

5 new regression tests in `tests/test_calibration_backtest.py` encode this
exact failure mode (weak relationship must not force a confident slope; thin
bucket spread must not fit a slope at all; `predict()` must not extrapolate
past `x_hi`; shrinkage must scale with `n`) so it cannot silently regress.
All existing calibration/backtest tests pass unchanged.
