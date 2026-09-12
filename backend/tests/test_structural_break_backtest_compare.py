"""
app/structural_break/backtest_compare.py -- walk-forward baseline-static vs
structural-break-adaptive comparison. Pure in-memory row lists, no DB.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.structural_break.backtest_compare import compare_static_vs_adaptive  # noqa: E402


def _rows(n, *, win_rate=0.5, points_win=10.0, points_loss=-8.0, mfe_win=12.0,
          mfe_loss=2.0, probability=0.6, start=0):
    out = []
    for i in range(n):
        is_win = ((start + i) % 10) < round(win_rate * 10)
        out.append({
            "probability": probability, "outcome": "WIN" if is_win else "LOSS",
            "points": points_win if is_win else points_loss,
            "mfe": mfe_win if is_win else mfe_loss,
            "created_ts": f"2026-09-{1 + (start + i) // 500:02d}T{(start + i) % 24:02d}:00:00Z",
        })
    return out


def test_healthy_sequence_never_gates_any_trades():
    rows = _rows(200, win_rate=0.55)
    report = compare_static_vs_adaptive(rows)
    assert report.n_trades_gated == 0
    assert report.n_model_switches == 0
    assert report.baseline["n"] == report.adaptive["n"] == 200


def test_single_category_losing_streak_alone_is_never_gated():
    """Mirrors break_score.py's own central safety property
    (test_small_losing_streak_alone_never_reaches_structural_break) in the
    walk-forward backtest context: probability stays well-calibrated
    (~0.6 predicted, matching a real ~0.5-0.6 win rate here) so ONLY the
    performance category can ever fire -- never enough on its own to reach
    a HALT state, so nothing gets gated."""
    healthy = _rows(200, win_rate=0.6, points_win=10.0, points_loss=-6.0, start=0)
    bad = _rows(300, win_rate=0.05, points_win=10.0, points_loss=-15.0,
                probability=0.55, start=200)   # probability tracks the real (low) win rate here
    report = compare_static_vs_adaptive(healthy + bad)
    assert report.n_model_switches == 0
    assert report.n_trades_gated == 0


def test_a_sustained_multi_category_break_gets_gated_and_helps_the_adaptive_path():
    # long healthy run, then a sharp losing patch that hits TWO independent
    # categories at once: performance craters (win rate 0.7 -> 0.0), AND
    # probability/mfe stay mismatched with the real outcome (calibration +
    # directional-accuracy drift) -- exactly the "spans >=2 categories"
    # condition break_score.py requires before it will ever reach a HALT
    # state. A fine tick_n=3 (vs. the default 20) gives the walk enough
    # evaluations to observe the sustained-evidence streak before the long
    # window's own trailing average gets diluted by the new regime.
    healthy = _rows(200, win_rate=0.7, points_win=15.0, points_loss=-3.0,
                     probability=0.6, start=0)
    bad = _rows(300, win_rate=0.0, points_win=10.0, points_loss=-20.0,
                probability=0.6, mfe_loss=-5.0, start=200)   # probability UNCHANGED, mfe now directionally wrong
    report = compare_static_vs_adaptive(healthy + bad, tick_n=3)
    assert report.n_model_switches >= 1
    assert report.n_trades_gated > 0
    # gating strips out (at least some of) the worst patch -> adaptive should
    # never be net WORSE than baseline on this deliberately-lopsided sequence
    assert report.adaptive["expectancy_points"] >= report.baseline["expectancy_points"]


def test_report_flags_ground_truth_metrics_as_honestly_uncomputable():
    report = compare_static_vs_adaptive(_rows(50))
    assert report.false_structural_break_detections is None
    assert report.missed_structural_breaks is None
    assert "ground-truth" in report.note


def test_short_history_produces_no_ticks_and_no_gating():
    rows = _rows(10)   # below min_rows_before_first_tick default (30)
    report = compare_static_vs_adaptive(rows)
    assert report.n_ticks == 0
    assert report.n_trades_gated == 0
    assert report.baseline["n"] == report.adaptive["n"] == 10


def test_empty_history_does_not_crash():
    report = compare_static_vs_adaptive([])
    assert report.n_rows == 0
    assert report.n_ticks == 0
    assert report.baseline["n"] == 0
