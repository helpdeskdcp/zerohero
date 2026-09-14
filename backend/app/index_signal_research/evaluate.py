"""
Phase 6/7 -- chronological TRAIN/VALIDATION/OOS evaluation of one
hypothesis's signal against the Phase-4 label, with an explicit,
leakage-safe baseline (decided on TRAIN only, then applied unchanged to
VALIDATION/OOS -- never recomputed on the split being scored, which would
itself be a leak) and per-year / per-time-bucket stability.

MULTIPLE-HYPOTHESIS CAVEAT (Phase 7's own explicit requirement): 8 signal
variants are tested in this mission (A, B, 2x C, D, E, F, G). No single
hypothesis's OOS p-value should be read at the nominal alpha=0.05 -- a
Bonferroni-corrected threshold of 0.05/8 = 0.00625 is used by
`passes_bar()` below, and the raw p-value is always reported alongside it
so a reader can see both.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

N_HYPOTHESES_TESTED = 8
BONFERRONI_ALPHA = 0.05 / N_HYPOTHESES_TESTED
MIN_SIGNALS_FOR_YEAR_REPORT = 20


def chronological_split(n: int, *, train_frac: float = 0.5, val_frac: float = 0.25):
    a, b = int(n * train_frac), int(n * (train_frac + val_frac))
    return slice(0, a), slice(a, b), slice(b, n)


@dataclass
class SplitResult:
    n_signals: int
    n_up: int
    n_down: int
    n_label_up: int
    n_label_down: int
    n_label_range: int
    wins: int
    accuracy: float | None
    baseline_accuracy: float | None
    p_value: float | None
    p_value_vs_half: float | None

    def to_dict(self) -> dict:
        return asdict(self)


def _score_split(signal: pd.Series, label: pd.Series, majority_dir: str | None) -> SplitResult:
    mask = signal.notna()
    n_signals = int(mask.sum())
    if n_signals == 0:
        return SplitResult(0, 0, 0, 0, 0, 0, 0, None, None, None, None)
    sub_signal = signal[mask]
    sub_label = label[mask]
    n_up = int((sub_signal == "UP").sum())
    n_down = int((sub_signal == "DOWN").sum())
    wins = int((sub_signal == sub_label).sum())
    accuracy = wins / n_signals
    baseline_accuracy = None
    p_value = None
    if majority_dir is not None:
        baseline_accuracy = float((sub_label == majority_dir).mean())
        try:
            p_value = float(scipy_stats.binomtest(wins, n_signals, p=baseline_accuracy).pvalue)
        except ValueError:
            p_value = None
    # SUPPLEMENTARY, STRICTER check found necessary during this mission's own
    # review: a TRAIN-decided single-direction baseline can drift out of sync
    # with a later split's true UP/DOWN base rate (e.g. TRAIN majority=DOWN,
    # but OOS actually leans UP) -- a signal that emits a MIX of UP/DOWN can
    # then look like it "beats baseline" purely from that drift, while still
    # being indistinguishable from a plain coin flip in absolute terms. Also
    # require beating flat p=0.5 -- this makes the bar STRICTER for every
    # hypothesis uniformly, never a loophole opened to help one pass.
    try:
        p_value_vs_half = float(scipy_stats.binomtest(wins, n_signals, p=0.5).pvalue)
    except ValueError:
        p_value_vs_half = None
    return SplitResult(
        n_signals=n_signals, n_up=n_up, n_down=n_down,
        n_label_up=int((sub_label == "UP").sum()), n_label_down=int((sub_label == "DOWN").sum()),
        n_label_range=int((sub_label == "RANGE").sum()), wins=wins,
        accuracy=round(accuracy, 4), baseline_accuracy=round(baseline_accuracy, 4) if baseline_accuracy is not None else None,
        p_value=round(p_value, 6) if p_value is not None else None,
        p_value_vs_half=round(p_value_vs_half, 6) if p_value_vs_half is not None else None,
    )


def evaluate_hypothesis(df: pd.DataFrame, label: pd.Series, signal: pd.Series, name: str) -> dict:
    n = len(df)
    train_sl, val_sl, oos_sl = chronological_split(n)
    train_signal, train_label = signal.iloc[train_sl], label.iloc[train_sl]
    train_mask = train_signal.notna()
    train_up = int((train_label[train_mask] == "UP").sum())
    train_down = int((train_label[train_mask] == "DOWN").sum())
    majority_dir = None
    if (train_up + train_down) > 0:
        majority_dir = "UP" if train_up >= train_down else "DOWN"

    splits = {
        "train": _score_split(train_signal, train_label, majority_dir),
        "validation": _score_split(signal.iloc[val_sl], label.iloc[val_sl], majority_dir),
        "oos": _score_split(signal.iloc[oos_sl], label.iloc[oos_sl], majority_dir),
    }

    oos = splits["oos"]
    train_r, val_r = splits["train"], splits["validation"]
    # NOTE: every comparison below uses explicit `is not None` checks, never
    # `x or default` -- a legitimate p-value/accuracy/baseline of exactly 0.0
    # is falsy in Python, so `0.0 or 1` silently becomes `1` and would
    # corrupt these comparisons (caught by this package's own tests).
    passes = bool(
        oos.accuracy is not None and oos.p_value is not None and oos.p_value_vs_half is not None
        and oos.baseline_accuracy is not None
        and oos.accuracy > oos.baseline_accuracy and oos.accuracy > 0.5
        and oos.p_value < BONFERRONI_ALPHA and oos.p_value_vs_half < BONFERRONI_ALPHA
        and oos.n_signals >= MIN_SIGNALS_FOR_YEAR_REPORT
        # stability, per Phase 20's own acceptance criteria: a hypothesis that
        # underperforms its OWN baseline on TRAIN, or shows no signal at all on
        # VALIDATION, is not "stable across periods" no matter what OOS shows
        and train_r.accuracy is not None and train_r.baseline_accuracy is not None
        and train_r.accuracy >= train_r.baseline_accuracy
        and val_r.p_value is not None and val_r.p_value < 0.10
    )

    per_year = {}
    oos_df_idx = df.index[oos_sl]
    for year, year_idx in df.loc[oos_df_idx].groupby("year").groups.items():
        sig_y, lab_y = signal.loc[year_idx], label.loc[year_idx]
        n_sig = int(sig_y.notna().sum())
        if n_sig < MIN_SIGNALS_FOR_YEAR_REPORT:
            per_year[int(year)] = {"n_signals": n_sig, "status": "INSUFFICIENT_SAMPLE"}
            continue
        r = _score_split(sig_y, lab_y, majority_dir)
        per_year[int(year)] = {"n_signals": r.n_signals, "accuracy": r.accuracy, "baseline_accuracy": r.baseline_accuracy}

    per_bucket = {}
    for bucket in ("OPENING", "MID", "CLOSING"):
        bmask = df.loc[oos_df_idx, "time_bucket"] == bucket
        idx = oos_df_idx[bmask.to_numpy()]
        sig_b, lab_b = signal.loc[idx], label.loc[idx]
        n_sig = int(sig_b.notna().sum())
        if n_sig < MIN_SIGNALS_FOR_YEAR_REPORT:
            per_bucket[bucket] = {"n_signals": n_sig, "status": "INSUFFICIENT_SAMPLE"}
            continue
        r = _score_split(sig_b, lab_b, majority_dir)
        per_bucket[bucket] = {"n_signals": r.n_signals, "accuracy": r.accuracy, "baseline_accuracy": r.baseline_accuracy}

    return {
        "hypothesis": name,
        "majority_dir_from_train": majority_dir,
        "splits": {k: v.to_dict() for k, v in splits.items()},
        "oos_per_year": per_year,
        "oos_per_time_bucket": per_bucket,
        "bonferroni_alpha": BONFERRONI_ALPHA,
        "passes_bar": passes,
        "bar_rationale": (
            f"OOS accuracy {oos.accuracy} vs baseline {oos.baseline_accuracy}, p={oos.p_value} "
            f"(Bonferroni alpha={BONFERRONI_ALPHA:.5f}, n={oos.n_signals})"
            if oos.accuracy is not None else "no OOS signals fired"
        ),
    }
