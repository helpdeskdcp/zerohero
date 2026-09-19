"""
app/index_signal_research/evaluate.py -- chronological split correctness,
leakage-safe baseline (decided on TRAIN, applied UNCHANGED to VAL/OOS), and
the passes_bar decision rule, using synthetic signal/label series so the
evaluation LOGIC is verified independent of any real hypothesis or dataset.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.index_signal_research.evaluate import (
    BONFERRONI_ALPHA,
    N_HYPOTHESES_TESTED,
    chronological_split,
    evaluate_hypothesis,
)


def _df(n):
    return pd.DataFrame({"year": [2024] * n, "time_bucket": ["MID"] * n})


def test_chronological_split_never_shuffles():
    train_sl, val_sl, oos_sl = chronological_split(100, train_frac=0.5, val_frac=0.25)
    assert train_sl == slice(0, 50) and val_sl == slice(50, 75) and oos_sl == slice(75, 100)


def test_bonferroni_alpha_matches_documented_hypothesis_count():
    assert N_HYPOTHESES_TESTED == 8
    assert abs(BONFERRONI_ALPHA - 0.05 / 8) < 1e-12


def test_a_real_edge_signal_passes_the_bar():
    """Signal ALWAYS correctly predicts the label (perfect synthetic edge,
    large n) -- must clearly pass on OOS."""
    n = 400
    df = _df(n)
    label = pd.Series((["UP", "DOWN"] * (n // 2)), dtype=object)
    signal = pd.Series((["UP", "DOWN"] * (n // 2)), dtype=object)   # always matches label
    result = evaluate_hypothesis(df, label, signal, "PERFECT_EDGE")
    assert result["passes_bar"] is True
    assert result["splits"]["oos"]["accuracy"] == 1.0


def test_a_coin_flip_signal_does_not_pass_the_bar():
    import random
    random.seed(0)
    n = 400
    df = _df(n)
    label = pd.Series([random.choice(["UP", "DOWN", "RANGE"]) for _ in range(n)], dtype=object)
    signal = pd.Series([random.choice(["UP", "DOWN"]) for _ in range(n)], dtype=object)
    result = evaluate_hypothesis(df, label, signal, "COIN_FLIP")
    assert result["passes_bar"] is False


def test_perfect_edge_with_zero_pvalue_still_passes_the_falsy_zero_check():
    """Regression test for a real bug found during this mission: a
    perfect-accuracy signal produces p_value=0.0 and validation p_value=0.0
    exactly -- `x or default`-style checks silently treat 0.0 as falsy and
    substitute the default (e.g. `0.0 or 1` == 1), which previously flipped
    `val_r.p_value < 0.10` into `1 < 0.10` == False and failed this exact
    case. passes_bar must use explicit `is not None` checks instead."""
    n = 400
    df = _df(n)
    label = pd.Series((["UP", "DOWN"] * (n // 2)), dtype=object)
    signal = pd.Series((["UP", "DOWN"] * (n // 2)), dtype=object)
    result = evaluate_hypothesis(df, label, signal, "PERFECT_EDGE_ZERO_P")
    assert result["splits"]["validation"]["p_value"] == 0.0
    assert result["passes_bar"] is True


def test_baseline_is_decided_on_train_only_and_applied_unchanged_to_oos():
    """TRAIN has more UP than DOWN among its signals -> majority_dir="UP".
    OOS's baseline_accuracy must equal OOS's own UP-label rate (the TRAIN-
    decided rule applied mechanically), NOT recomputed from whatever OOS's
    own majority happens to be."""
    n = 200
    df = _df(n)
    # TRAIN half (0:100): signal fires everywhere, label is UP 80% of the time
    train_labels = ["UP"] * 80 + ["DOWN"] * 20
    # OOS half (150:200 after val 100:150): label is DOWN 80% of the time -- OPPOSITE skew
    val_labels = ["UP"] * 25 + ["DOWN"] * 25
    oos_labels = ["DOWN"] * 40 + ["UP"] * 10
    label = pd.Series(train_labels + val_labels + oos_labels, dtype=object)
    signal = pd.Series(["UP"] * n, dtype=object)
    result = evaluate_hypothesis(df, label, signal, "SKEW_TEST")
    assert result["majority_dir_from_train"] == "UP"
    # baseline on OOS must reflect applying "UP" as the fixed guess to OOS's
    # OWN label distribution (10/50 = 0.2), not OOS's own majority (which is DOWN)
    assert result["splits"]["oos"]["baseline_accuracy"] == 0.2
