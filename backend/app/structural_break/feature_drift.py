"""
Feature / distribution drift monitor -- section B of the Structural Break
spec.

Deliberately reads features ALREADY CAPTURED per-decision in `scalp_signals`
(atr, pcr, momentum, vwap, index_ltp, mtf_alignment, regime,
component_scores JSON, expected_premium_move) rather than hooking into
sr_engine/option_engine/optionchain live -- this module doesn't compute a
single feature itself, it only watches ones other engines already computed
and already logged. That's what "reuse existing engines, don't duplicate"
means concretely here.

Method-per-feature allocation (why, not just what -- continuing the
reasoning started in drift.py's module docstring):

  - CUSUM        : PCR, momentum, mtf_alignment -- roughly-stationary,
                   two-sided continuous signals where an ABRUPT mean shift
                   (either direction) is the meaningful event.
  - KS (2-sample): ATR, VWAP-distance, expected_premium_move -- continuous
                   features where the SHAPE of the distribution (spread,
                   skew) can change even if the mean barely moves --
                   volatility-like series are exactly this case.
  - PSI          : regime label -- categorical by construction, PSI is the
                   standard tool for a categorical mix shift.
  - Rolling z-score: the component_scores sub-scores (volume, oi) -- these
                   are already-normalised 0-100-ish scores with typically
                   less history per key than the other features; a direct
                   z-score against a fit baseline is the simplest thing that
                   doesn't require a large enough sample for KS to be
                   reliable (recall drift.py's PSI note: too few
                   samples/bin makes bin-based methods noisy).

Page-Hinkley (persistent one-directional drift) is deliberately NOT used
here -- that behavior belongs to prediction_drift.py, where "the error is
persistently getting worse" (not "the input distribution changed") is
exactly what section C is asking to detect. Feature drift here is symmetric:
a market feature can shift either way and both matter equally.

Everything below is a pure function/class over an already-fetched row list
(same "stateless per call, re-fit baseline vs current window every time"
style as performance_monitor.py) -- no long-lived detector instances, no
extra DB table, nothing wired to any decision.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field

from .drift import CusumDetector, PsiTracker, fit_baseline, ks_2sample

DEFAULT_BASELINE_N = 150
DEFAULT_CURRENT_N = 30


def _f(x):
    try:
        v = float(x)
        return v if v == v and math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _component(row, key):
    raw = row.get("component_scores")
    if not raw:
        return None
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
        return _f(d.get(key))
    except (ValueError, TypeError, AttributeError):
        return None


def _vwap_distance_pct(row):
    ltp, vwap = _f(row.get("index_ltp")), _f(row.get("vwap"))
    if ltp is None or vwap is None or vwap == 0:
        return None
    return (ltp - vwap) / vwap * 100.0


# name -> (extractor(row) -> float|None, method)
FEATURE_SPECS = {
    "atr": (lambda r: _f(r.get("atr")), "ks"),
    "pcr": (lambda r: _f(r.get("pcr")), "cusum"),
    "momentum": (lambda r: _f(r.get("momentum")), "cusum"),
    "mtf_alignment": (lambda r: _f(r.get("mtf_alignment")), "cusum"),
    "vwap_distance_pct": (_vwap_distance_pct, "ks"),
    "expected_premium_move": (lambda r: _f(r.get("expected_premium_move")), "ks"),
    "regime": (lambda r: r.get("regime"), "psi"),
    "volume_score": (lambda r: _component(r, "volume"), "zscore"),
    "oi_score": (lambda r: _component(r, "oi"), "zscore"),
}

# Not currently monitorable from scalp_signals as captured today -- named
# honestly rather than faked:
#   - "volatility" beyond ATR, and "range position" have no dedicated column
#     or component_scores key at signal-decision time.
#   - "IV" (implied vol) lives in greek_exposure (oi_weighted_iv /
#     vega_weighted_iv), a separate table on a separate cadence from
#     scalp_signals -- worth a follow-up monitor reading that table directly
#     if this layer is extended, not bolted on here as a mismatched join.
UNMONITORED_FEATURES_NOTE = (
    "volatility (beyond ATR) and range-position have no captured column; "
    "IV lives in greek_exposure on a different cadence, not joined here"
)


@dataclass
class FeatureResult:
    feature: str
    method: str
    status: str
    triggered: bool = False
    detail: dict = field(default_factory=dict)

    def to_dict(self):
        d = asdict(self)
        return d


def _split_baseline_current(rows: list[dict], baseline_n: int, current_n: int):
    """rows must be chronological (oldest first). Baseline = the window
    immediately BEFORE the current window, never overlapping it -- comparing
    a window against itself would trivially never show drift."""
    if len(rows) < current_n + 5:
        return [], rows[-current_n:] if rows else []
    current = rows[-current_n:]
    baseline_pool = rows[:-current_n]
    baseline = baseline_pool[-baseline_n:]
    return baseline, current


def evaluate_feature_drift(rows: list[dict], *, baseline_n: int = DEFAULT_BASELINE_N,
                            current_n: int = DEFAULT_CURRENT_N) -> dict:
    """rows: chronological (oldest first) scalp_signals-shaped dicts, already
    fetched by the caller (e.g. performance_monitor._resolved_rows, or any
    row source with the same columns) -- this function does no DB I/O."""
    baseline, current = _split_baseline_current(rows, baseline_n, current_n)
    out = {"n_baseline": len(baseline), "n_current": len(current), "features": {}}
    if len(baseline) < 20 or len(current) < 10:
        out["status"] = "INSUFFICIENT_DATA"
        return out
    out["status"] = "OK"

    for name, (extractor, method) in FEATURE_SPECS.items():
        b_vals = [v for r in baseline if (v := extractor(r)) is not None]
        c_vals = [v for r in current if (v := extractor(r)) is not None]
        if len(b_vals) < 10 or len(c_vals) < 5:
            out["features"][name] = FeatureResult(
                feature=name, method=method, status="insufficient_samples").to_dict()
            continue

        if method == "ks":
            r = ks_2sample(b_vals, c_vals)
            out["features"][name] = FeatureResult(
                feature=name, method=method, status=r.get("status", "ok"),
                triggered=bool(r.get("triggered")), detail=r).to_dict()

        elif method == "cusum":
            base = fit_baseline(b_vals)
            det = CusumDetector(baseline_mean=base["mean"], baseline_sigma=base["sigma"] or 1e-6)
            triggered = False
            last = None
            for v in c_vals:
                last = det.update(v)
                triggered = triggered or last["triggered"]
            out["features"][name] = FeatureResult(
                feature=name, method=method, status="ok", triggered=triggered,
                detail={**(last or {}), "baseline_mean": round(base["mean"], 4),
                        "baseline_sigma": round(base["sigma"], 4)}).to_dict()

        elif method == "psi":
            # categorical: PSI over the label mix (bin = category, not a
            # quantile edge) -- reuse PsiTracker's bin-percent machinery by
            # treating each distinct baseline label as its own "bin"
            labels = sorted(set(b_vals) | set(c_vals))
            b_pct = [b_vals.count(lb) / len(b_vals) for lb in labels]
            c_pct = [c_vals.count(lb) / len(c_vals) for lb in labels]
            eps = 1e-4
            psi = sum((c - b) * math.log(max(c, eps) / max(b, eps)) for c, b in zip(c_pct, b_pct))
            level = "significant" if psi >= 0.25 else "moderate" if psi >= 0.10 else "stable"
            out["features"][name] = FeatureResult(
                feature=name, method=method, status="ok", triggered=psi >= 0.25,
                detail={"psi": round(psi, 5), "level": level, "labels": labels,
                        "baseline_mix": dict(zip(labels, (round(x, 3) for x in b_pct))),
                        "current_mix": dict(zip(labels, (round(x, 3) for x in c_pct)))}).to_dict()

        elif method == "zscore":
            base = fit_baseline(b_vals)
            cur_mean = sum(c_vals) / len(c_vals)
            sigma = max(base["sigma"], 1e-9)
            z = (cur_mean - base["mean"]) / sigma
            out["features"][name] = FeatureResult(
                feature=name, method=method, status="ok", triggered=abs(z) > 3.0,
                detail={"z": round(z, 4), "current_mean": round(cur_mean, 4),
                        "baseline_mean": round(base["mean"], 4)}).to_dict()

    return out
