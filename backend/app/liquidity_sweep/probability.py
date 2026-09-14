"""
Probability + confidence -- sections 6-8: calibrated P(UP)/P(DOWN)/P(RANGE),
kept separate from a distinct CONFIDENCE score, per the brief's own
explicit "probability and confidence are NOT the same thing."

Probability is NOT a raw score-to-percentage conversion (the brief
explicitly forbids that) -- it reuses app.backtest.calibration's existing
fit()/predict()/reliability_curve() (score bucketed against REAL historical
win-rate, closed-form logistic fit, already used and validated by the live
autoscalp calibration pipeline) rather than inventing a second calibration
method. `fit_index_calibration()`/`predict_directional_probability()` are
thin wrappers naming that reuse explicitly for this package's callers.

Three-way split (P(UP)+P(DOWN)+P(RANGE)): calibration.predict() gives
P(the candidate direction wins) for ONE candidate at a time -- it has no
native concept of a third "range" outcome. The split below is a documented
modeling choice, not a second calibrated model: `range_share` of the
"doesn't win" probability mass is attributed to RANGE (chop/no clean move),
the rest to the opposite direction. Default 0.35 is a plain, stated
constant -- backtest.py's own calibration report should be read alongside
this, not in place of it, before trusting the RANGE figure specifically.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ..backtest import calibration as _calib

DEFAULT_RANGE_SHARE = 0.35


def fit_index_calibration(samples, *, version: str | None = None) -> dict:
    """samples: [{"score": 0-100, "regime": str, "signal_type": str, "win": bool}, ...]."""
    return _calib.fit(samples, version=version)


def predict_directional_probability(calib: dict, score_0_100: float, *,
                                     regime: str = "?", signal_type: str = "?") -> float:
    return _calib.predict(calib, score_0_100, regime=regime, signal_type=signal_type)


def reliability_report(pairs, bins: int = 10) -> dict:
    return _calib.reliability_curve(pairs, bins=bins)


@dataclass
class ProbabilityTriple:
    up: float
    down: float
    range: float

    def to_dict(self) -> dict:
        return asdict(self)


def three_way(p_directional: float, direction: str, *, range_share: float = DEFAULT_RANGE_SHARE) -> ProbabilityTriple:
    p_directional = max(0.0, min(1.0, p_directional))
    remainder = 1.0 - p_directional
    p_range = remainder * range_share
    p_other = remainder - p_range
    if direction == "BULLISH":
        return ProbabilityTriple(up=round(p_directional, 4), down=round(p_other, 4), range=round(p_range, 4))
    return ProbabilityTriple(up=round(p_other, 4), down=round(p_directional, 4), range=round(p_range, 4))


def confidence_score(*, setup_passed_checks: int, setup_max_checks: int,
                      sweep_reaction_atr_ratio: float | None, htf_bias_score: float) -> float:
    """0-100, combining three named, independently-inspectable components
    (equal thirds -- a plain, documented split, not fitted):
      - setup-check completeness (how many of setup_score's checks passed)
      - sweep quality (how far beyond the level the wick travelled, in ATR)
      - HTF conviction (|htf_bias score|, already in [-1,1])
    Capped/clamped so a single extreme input can't blow past 100."""
    setup_frac = setup_passed_checks / setup_max_checks if setup_max_checks else 0.0
    sweep_frac = min(1.0, (sweep_reaction_atr_ratio or 0.0) / 1.0)   # 1x ATR reaction -> full marks
    htf_frac = min(1.0, abs(htf_bias_score))
    return round(100.0 * (setup_frac + sweep_frac + htf_frac) / 3.0, 1)


def label_outcome(bars_after: list[dict], *, entry: float, direction: str,
                   threshold_pts: float, horizon: int) -> str:
    """BACKTEST-ONLY (never called from live scoring): walks FORWARD through
    `bars_after` (the horizon window, already sliced by the caller) to
    determine whether price reached `entry + threshold_pts` (BULLISH) or
    `entry - threshold_pts` (BEARISH) before the opposite threshold, within
    `horizon` bars. "WIN" / "LOSS" / "TIMEOUT" (touched neither threshold
    within the horizon -- genuinely a RANGE/indecisive outcome, not folded
    into WIN or LOSS)."""
    up_target = entry + threshold_pts
    down_target = entry - threshold_pts
    for bar in bars_after[:horizon]:
        hit_up = bar["h"] >= up_target
        hit_down = bar["l"] <= down_target
        if hit_up and hit_down:
            # a single bar spanning both thresholds -- can't know which was
            # touched first intrabar, so assume the worst case (a LOSS)
            # rather than guess: same conservative convention
            # app.orderflow.h1h7_state already uses for this exact ambiguity
            # ("a bar spanning both -> STOP assumed").
            return "LOSS"
        if hit_up:
            return "WIN" if direction == "BULLISH" else "LOSS"
        if hit_down:
            return "WIN" if direction == "BEARISH" else "LOSS"
    return "TIMEOUT"
