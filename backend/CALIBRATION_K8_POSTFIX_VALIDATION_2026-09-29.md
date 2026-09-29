# K8 Calibration Fix -- Post-Deploy Validation (2026-09-29)

Follow-up to `data/research/calibration_fix_2026-09/VERDICT.md` (the 2026-09-21
fix). That entry validated the fix offline (chronological train/test split on
153 historical trades, refit with the new code). This entry checks whether the
fix is actually behaving correctly **in live production**, 8 days after deploy,
using the official diagnostic (`app.autoscalp.calibration_report`).

## What a fresh run of the official diagnostic shows today

`calibration_report()`, n=195 resolved LIVE trades: still verdict
**OVERCONFIDENT**, ECE 0.1865, reliability table still shows the same
inversion at high bins (0.7-0.8: pred=0.769 actual=0.381; 0.8-0.9: pred=0.830
actual=0.294) as the pre-fix numbers in VERDICT.md.

**This does not mean the fix failed.** `calibration_report()` reads each
trade's `probability` as it was stored at signal-creation time -- a snapshot
of whatever model produced it, not a live recompute. Of the 195 resolved
trades, the overwhelming majority were created before the 2026-09-21 fix and
carry pre-fix, floor-forced predictions that will never change retroactively.

## Splitting by fix date

Resolved WIN/LOSS trades (n=167), split at the fix commit (`04136c7`,
2026-09-21 09:59:45 UTC):

```
PRE-FIX  (n=154): mean_pred=0.617  actual_wr=0.494  brier=0.2994
    [0.7-0.8) n=38  pred=0.773  actual=0.421   <- inversion
    [0.8-1.0] n=13  pred=0.830  actual=0.385   <- inversion

POST-FIX (n=13):  mean_pred=0.504  actual_wr=0.615  brier=0.2498
    (no predictions above 0.6 at all)
```

Post-fix, the model can no longer produce a confident prediction above ~0.6 --
exactly the designed effect of removing the `k` floor and adding shrinkage
(the live stored curve as of today: `global: k=0.0044, b=0.0162, n=167`,
essentially flat, which is the honest answer given this system's real,
repeatedly-backtested weak edge). n=13 is far too thin to declare victory on
Brier/ECE alone, but the *qualitative* pattern the fix targeted -- confident
predictions in bins the model has no support for -- is gone. This is the
expected, designed behavior working correctly, not yet a statistically
decided outcome.

## A real bug found while tracing this (fixed)

The DB has a `calib_version` column meant to stamp which model version
produced each prediction -- it would have made this whole before/after split
trivial (`WHERE calib_version = 'scalp-calibration-v2'` instead of a manual
timestamp cutoff). It was **NULL on all 195 resolved rows, including ones
created today** -- looked like recalibration wasn't reaching production at
all. Traced to `app/autoscalp/runner.py`'s `insert_scalp_signal()` call: the
row dict is built by manually copying ~35 fields off `sig` (the
`decide_from_context()` result) one at a time, and `calib_version` was simply
missing from that list -- present on `sig`, silently dropped before it ever
reached the DB. `app/backtest/replay.py`'s equivalent call already included
it correctly; only the live runner path had the gap. Fixed (one field added,
`runner.py`), 111 autoscalp/calibration/runner tests still pass, deployed via
`systemctl restart oi-dashboard.service` (0 open positions at restart time).
Going forward, `calib_version` will actually reflect reality and this kind of
audit won't need a manual date-split.

## Bottom line

- The K8 fix (`04136c7`) is live and behaving as designed in production, not
  just in the offline validation.
- The official `calibration_report()` diagnostic will keep reporting
  OVERCONFIDENT for a while yet -- it's an aggregate over trades resolved as
  far back as 2026-08-31, and pre-fix predictions don't get rewritten. It will
  improve gradually as more post-fix trades resolve; there's no single
  moment where it "flips."
- Re-run this same post-fix-only split periodically (or, now that
  `calib_version` is actually populated going forward, simply filter on it)
  to track real progress instead of reading the aggregate diagnostic as if it
  reflected current behavior.
- Sample is still thin (n=13 post-fix resolved trades) -- this is a
  directional confirmation the fix works, not a new calibration verdict on
  its own.
