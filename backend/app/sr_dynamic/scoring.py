"""
Requirement 10-11: combine swing strength, touch/rejection, volume
confirmation, recency, breakout/retest, and multi-timeframe confirmation
into a single 0-100 zone score.

This is a STRENGTH score, not a probability -- requirement 11 explicitly
forbids calling it one unless statistically calibrated, and it has not
been (no historical outcome data was fit against it). Do not rename this
field or expose it as a win-probability without running the same
calibration discipline app.backtest.calibration already applies elsewhere
in this codebase.

Follows this codebase's established renormalization pattern (see
app.strategy.scoring's bucket scorer, and app.strategy_mtf.mtf_aggregator's
fix for the same issue): a component with no real data to judge it is
EXCLUDED from both the numerator and denominator, never fabricated as a
neutral guess and never counted as a failing 0.
"""
from __future__ import annotations

from dataclasses import dataclass

WEIGHTS = {
    "swing_strength": 0.25,
    "touch_rejection": 0.20,
    "volume_confirmation": 0.20,
    "recency": 0.15,
    "breakout_retest": 0.10,
    "mtf_confirmation": 0.10,
}

_RETEST_SCORE = {"NONE": 0.5, "PENDING": 0.5, "SUCCESSFUL": 1.0, "FAILED": 0.1}


@dataclass
class ZoneScore:
    strength: float                    # 0..100
    components: dict                   # component -> 0..1 (only the ones actually scored)


def score_zone(*, swing_count: int, touches: int, rejections: int,
              volume_ratio: float | None, last_touch_index: int | None, n_bars: int,
              retest_status: str, confirmed_other_tfs: int | None, n_other_tfs_available: int | None,
              max_swing_count: int = 4) -> ZoneScore:
    c: dict[str, float | None] = {}

    c["swing_strength"] = min(1.0, swing_count / max_swing_count) if swing_count else 0.0

    if touches > 0:
        touch_quality = rejections / touches
        rejection_count_score = min(1.0, rejections / 3.0)
        c["touch_rejection"] = (touch_quality + rejection_count_score) / 2.0
    else:
        c["touch_rejection"] = None          # never touched -- nothing to judge yet

    c["volume_confirmation"] = min(1.0, volume_ratio / 2.0) if volume_ratio is not None else None

    if last_touch_index is not None and n_bars > 0:
        age = (n_bars - 1 - last_touch_index)
        c["recency"] = max(0.0, 1.0 - age / n_bars)
    else:
        c["recency"] = None

    c["breakout_retest"] = _RETEST_SCORE.get(retest_status, 0.5)

    if n_other_tfs_available:
        c["mtf_confirmation"] = min(1.0, (confirmed_other_tfs or 0) / n_other_tfs_available)
    else:
        c["mtf_confirmation"] = None         # no other-timeframe data to check against

    available = {k: v for k, v in c.items() if v is not None}
    denom = sum(WEIGHTS[k] for k in available) or 1e-9
    numer = sum(WEIGHTS[k] * v for k, v in available.items())
    strength = round(max(0.0, min(100.0, 100.0 * numer / denom)), 1)

    return ZoneScore(strength=strength, components={k: round(v, 3) for k, v in available.items()})
