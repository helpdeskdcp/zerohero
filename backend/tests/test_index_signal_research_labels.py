"""
app/index_signal_research/labels.py -- the Phase 4 outcome definition.
Synthetic bars only (real-data run is the driver script, not a unit test).
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.index_signal_research.labels import compute_labels  # noqa: E402


def _df(rows):
    return pd.DataFrame(rows, columns=["o", "h", "l", "c", "atr14"])


def test_up_when_high_reaches_threshold_before_low_does():
    rows = [
        {"o": 100, "h": 100.2, "l": 99.8, "c": 100.0, "atr14": 1.0},   # signal bar, atr=1 -> up=101, down=99
        {"o": 100, "h": 100.5, "l": 99.9, "c": 100.3, "atr14": 1.0},
        {"o": 100.3, "h": 101.5, "l": 100.1, "c": 101.2, "atr14": 1.0},  # hits UP (101)
        {"o": 101.2, "h": 101.3, "l": 100.9, "c": 101.0, "atr14": 1.0},
    ]
    labels = compute_labels(pd.DataFrame(rows), horizon=3)
    assert labels.iloc[0] == "UP"


def test_down_when_low_reaches_threshold_first():
    rows = [
        {"o": 100, "h": 100.2, "l": 99.8, "c": 100.0, "atr14": 1.0},
        {"o": 100, "h": 100.3, "l": 99.5, "c": 99.7, "atr14": 1.0},
        {"o": 99.7, "h": 100.0, "l": 98.9, "c": 99.0, "atr14": 1.0},   # hits DOWN (99)
        {"o": 99.0, "h": 99.2, "l": 98.8, "c": 99.0, "atr14": 1.0},
    ]
    labels = compute_labels(pd.DataFrame(rows), horizon=3)
    assert labels.iloc[0] == "DOWN"


def test_range_when_neither_threshold_reached():
    rows = [{"o": 100, "h": 100.2, "l": 99.8, "c": 100.0, "atr14": 1.0}]
    rows += [{"o": 100.0, "h": 100.3, "l": 99.7, "c": 100.0, "atr14": 1.0} for _ in range(3)]
    labels = compute_labels(pd.DataFrame(rows), horizon=3)
    assert labels.iloc[0] == "RANGE"


def test_ambiguous_single_bar_hitting_both_thresholds_is_range_not_a_forced_call():
    rows = [
        {"o": 100, "h": 100.2, "l": 99.8, "c": 100.0, "atr14": 1.0},   # up=101, down=99
        {"o": 100.0, "h": 102.0, "l": 98.0, "c": 100.0, "atr14": 1.0},  # spans BOTH in one bar
    ]
    labels = compute_labels(pd.DataFrame(rows), horizon=1)
    assert labels.iloc[0] == "RANGE"


def test_trailing_incomplete_window_rows_are_none_not_range():
    rows = [{"o": 100, "h": 100.2, "l": 99.8, "c": 100.0, "atr14": 1.0} for _ in range(5)]
    labels = compute_labels(pd.DataFrame(rows), horizon=3)
    assert labels.iloc[-1] is None
    assert labels.iloc[-2] is None
    assert labels.iloc[-3] is None
    assert labels.iloc[1] is not None   # a row WITH a complete forward window must be graded


def test_invalid_atr_does_not_crash_and_is_reported_as_range():
    rows = [{"o": 100, "h": 100.2, "l": 99.8, "c": 100.0, "atr14": float("nan")}]
    rows += [{"o": 100.0, "h": 105.0, "l": 95.0, "c": 100.0, "atr14": 1.0} for _ in range(3)]
    labels = compute_labels(pd.DataFrame(rows), horizon=3)
    assert labels.iloc[0] == "RANGE"
