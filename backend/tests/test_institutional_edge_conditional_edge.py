"""
app/institutional_edge/conditional_edge.py -- P(win|condition) - P(win|baseline).
Pure in-memory rows, no DB.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.institutional_edge.conditional_edge import (  # noqa: E402
    MIN_SAMPLE_N, conditional_edge,
)


def _rows(n, *, win_rate=0.5, regime="TRENDING_UP", points_win=10.0, points_loss=-8.0, start=0):
    out = []
    for i in range(n):
        is_win = ((start + i) % 10) < round(win_rate * 10)
        out.append({"outcome": "WIN" if is_win else "LOSS", "regime": regime,
                    "points": points_win if is_win else points_loss})
    return out


def test_a_genuinely_better_condition_shows_a_positive_edge():
    # baseline mixes a 40%-win regime and a 70%-win regime -> unconditional ~55%
    baseline = _rows(60, win_rate=0.40, regime="RANGE", start=0) + \
        _rows(60, win_rate=0.70, regime="TRENDING_UP", start=60)
    result = conditional_edge(baseline, lambda r: r["regime"] == "TRENDING_UP",
                              condition_label="regime==TRENDING_UP")
    assert result.status == "OK"
    assert result.p_condition == 0.7
    assert 0.54 < result.p_baseline < 0.56
    assert result.conditional_edge > 0.1
    assert result.edge_ci95_low < result.conditional_edge < result.edge_ci95_high


def test_a_worse_condition_shows_a_negative_edge():
    baseline = _rows(60, win_rate=0.40, regime="RANGE", start=0) + \
        _rows(60, win_rate=0.70, regime="TRENDING_UP", start=60)
    result = conditional_edge(baseline, lambda r: r["regime"] == "RANGE",
                              condition_label="regime==RANGE")
    assert result.conditional_edge < 0


def test_insufficient_condition_sample_is_flagged_not_computed():
    baseline = _rows(100, win_rate=0.5) + [{"outcome": "WIN", "regime": "RARE", "points": 5.0}]
    result = conditional_edge(baseline, lambda r: r["regime"] == "RARE",
                              condition_label="regime==RARE")
    assert result.status == "INSUFFICIENT_DATA"
    assert result.n_condition == 1
    assert result.p_condition is None


def test_insufficient_baseline_sample_is_also_flagged():
    tiny = _rows(10, win_rate=0.5)
    result = conditional_edge(tiny, lambda r: True, condition_label="everything")
    assert result.status == "INSUFFICIENT_DATA"


def test_min_n_is_configurable():
    baseline = _rows(20, win_rate=0.5)
    result = conditional_edge(baseline, lambda r: True, condition_label="all", min_n=10)
    assert result.status == "OK"


def test_return_stats_computed_over_the_conditional_subset_only():
    baseline = _rows(60, win_rate=0.5, points_win=100.0, points_loss=-8.0, regime="A", start=0) + \
        _rows(60, win_rate=0.5, points_win=1.0, points_loss=-1.0, regime="B", start=60)
    result = conditional_edge(baseline, lambda r: r["regime"] == "A", condition_label="regime==A")
    assert result.mean_return_condition > 10   # dominated by the big-point-win regime, not regime B
    assert result.median_return_condition is not None
    assert result.variance_return_condition is not None


def test_rows_missing_a_resolved_outcome_are_excluded_from_both_populations():
    baseline = _rows(40, win_rate=0.5) + [{"outcome": "PENDING", "regime": "X"}] * 5
    result = conditional_edge(baseline, lambda r: True, condition_label="all")
    assert result.n_baseline == 40   # the 5 PENDING rows never counted
