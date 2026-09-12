"""
Model residual / prediction drift -- section C of the Structural Break spec.

Tracks three things explicitly named in the spec, each as a Page-Hinkley
stream (the ONE detector in this layer chosen specifically for "persistent
deterioration, not isolated errors" -- see drift.py's module docstring for
why Page-Hinkley is the right tool for that distinction and CUSUM/KS/PSI are
not):

  1. predicted direction vs actual direction  -> directional correctness,
     0/1 per trade (same MFE-based proxy as performance_monitor.py's
     directional_accuracy, for the same documented reason: no separate
     "underlying moved this way" column exists in scalp_signals today, so
     "outcome==WIN, or a LOSS whose MFE was still positive at some point" is
     the honest signal available). Watched for a PERSISTENT DECREASE.
  2. predicted probability vs realized outcome -> calibration error,
     abs(probability - win_bool) per trade. Watched for a PERSISTENT
     INCREASE. This re-uses the same quantity calibration_report.py already
     computes in aggregate (as MAE, alongside its Brier/ECE) -- here it's
     watched as a SEQUENCE instead of one aggregate number, which is what
     lets Page-Hinkley separate "consistently a bit off" from "getting worse."
  3. predicted move vs actual move -> abs(expected_premium_move - points)
     per trade, i.e. how far off the EPM (already computed by option_engine
     and logged on every signal) was from the option's actual realised
     point move. Watched for a PERSISTENT INCREASE. Raw points scale --
     like ev_r elsewhere in this codebase, comparing this number ACROSS
     symbols with very different premium scales (NIFTY vs NATURALGAS) isn't
     meaningful; call this per-symbol, same as performance_monitor.py.

Each stream: `fit_baseline` on the first `baseline_n` chronological rows
gives the SIGMA used to scale the detector's threshold (same "express
lambda in units of the metric's own spread" convention as everywhere else in
drift.py) -- but the PageHinkleyDetector instance itself is fed the
baseline rows FIRST, then the eval rows, as one continuous sequence. This
matters and was wrong in an earlier version of this file, caught by its own
tests: Page-Hinkley's running mean is computed OVER WHATEVER STREAM IT HAS
SEEN. Starting a fresh detector (n=0) exactly at the eval boundary means its
running mean adapts to the NEW level within the first few eval samples --
by the time you'd expect a persistent shift to register, the detector has
already "moved on" and sees no gap at all. Feeding it the baseline first
gives the running mean enough accumulated inertia (n already large) that it
resists being pulled to a new level immediately, which is what actually lets
it detect a persisted shift -- exactly the same pattern drift.py's own
calibration tests use (warm up a detector on stable data, THEN push the
shifted values into that SAME instance, never a fresh one).

Not a live-updating singleton -- stateless per call, same convention as
performance_monitor.py and feature_drift.py (a fresh detector is built each
`evaluate_prediction_drift` call, it just isn't fresh AT the eval boundary).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .drift import PageHinkleyDetector, fit_baseline

DEFAULT_BASELINE_N = 100


def _f(x):
    try:
        v = float(x)
        return v if v == v and math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _directional_correct(row) -> float | None:
    if row.get("outcome") not in ("WIN", "LOSS"):
        return None
    if row["outcome"] == "WIN":
        return 1.0
    mfe = _f(row.get("mfe"))
    return 1.0 if (mfe is not None and mfe > 0) else 0.0


def _calibration_error(row) -> float | None:
    p, oc = _f(row.get("probability")), row.get("outcome")
    if p is None or oc not in ("WIN", "LOSS"):
        return None
    win = 1.0 if oc == "WIN" else 0.0
    return abs(max(0.0, min(1.0, p)) - win)


def _move_error(row) -> float | None:
    epm, pts = _f(row.get("expected_premium_move")), _f(row.get("points"))
    if epm is None or pts is None:
        return None
    return abs(epm - pts)


STREAMS = {
    "directional_accuracy": (_directional_correct, "decrease"),
    "calibration_error": (_calibration_error, "increase"),
    "move_error": (_move_error, "increase"),
}


@dataclass
class StreamResult:
    stream: str
    direction: str
    status: str
    triggered: bool = False
    fired_at_index: int | None = None      # index into the post-baseline rows, if it fired
    n_baseline: int = 0
    n_evaluated: int = 0
    baseline_mean: float | None = None
    baseline_sigma: float | None = None

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if v is not None or k in ("triggered", "status")}


def evaluate_prediction_drift(rows: list[dict], *, baseline_n: int = DEFAULT_BASELINE_N) -> dict:
    """rows: chronological (oldest first) scalp_signals-shaped dicts."""
    out = {"n_rows": len(rows), "streams": {}}
    if len(rows) < baseline_n + 10:
        out["status"] = "INSUFFICIENT_DATA"
        return out
    out["status"] = "OK"

    baseline_rows, eval_rows = rows[:baseline_n], rows[baseline_n:]

    for name, (extractor, direction) in STREAMS.items():
        b_vals = [v for r in baseline_rows if (v := extractor(r)) is not None]
        e_vals = [(i, v) for i, r in enumerate(eval_rows) if (v := extractor(r)) is not None]
        if len(b_vals) < 10 or len(e_vals) < 10:
            out["streams"][name] = StreamResult(
                stream=name, direction=direction, status="insufficient_samples").to_dict()
            continue

        base = fit_baseline(b_vals)
        sigma = base["sigma"] or 1e-6
        det = PageHinkleyDetector(baseline_sigma=sigma, direction=direction)
        for v in b_vals:            # build running-mean inertia from the baseline FIRST
            det.update(v)
        fired_at = None
        for i, v in e_vals:         # only trigger during the eval region is reported
            r = det.update(v)
            if r["triggered"] and fired_at is None:
                fired_at = i

        out["streams"][name] = StreamResult(
            stream=name, direction=direction, status="ok", triggered=fired_at is not None,
            fired_at_index=fired_at, n_baseline=len(b_vals), n_evaluated=len(e_vals),
            baseline_mean=round(base["mean"], 4), baseline_sigma=round(sigma, 4),
        ).to_dict()

    return out
