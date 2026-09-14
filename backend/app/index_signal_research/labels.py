"""
Phase 4 -- the outcome definition, REGISTERED HERE BEFORE any hypothesis in
hypotheses.py is evaluated, and never changed afterward based on results
(the mission brief's own explicit rule: "define the outcome before testing,
do not change it after seeing results").

HORIZON_BARS=12 (~1 hour on 5m) and ATR_THRESHOLD_MULT=1.0 are carried over
UNCHANGED from the frozen liquidity_sweep baseline
(`app.liquidity_sweep.backtest.HORIZON_BARS`/`THRESHOLD_PTS_ATR_MULT`) --
not re-picked for this mission, so any accuracy improvement found here is
comparable apples-to-apples against the frozen baseline's own number
(Phase 19's explicit requirement), not an artifact of choosing an easier
label definition.

UP:   future high within the horizon reaches close + ATR_THRESHOLD_MULT*atr
      before the DOWN threshold is reached.
DOWN: symmetric, downside.
RANGE: neither threshold is reached within the horizon, OR a single bar
      touches both thresholds (can't know which was hit first intrabar --
      the same conservative "ambiguous -> not a clean directional read"
      convention `app.liquidity_sweep.probability.label_outcome` already
      uses, adapted here for an UNCONDITIONAL market label rather than a
      graded trade outcome, so it resolves to RANGE, not to a forced
      win/loss).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HORIZON_BARS = 12
ATR_THRESHOLD_MULT = 1.0


def compute_labels(df: pd.DataFrame, *, horizon: int = HORIZON_BARS,
                    atr_mult: float = ATR_THRESHOLD_MULT, atr_col: str = "atr14") -> pd.Series:
    """Returns a Series of "UP" | "DOWN" | "RANGE" | None (None only for the
    trailing `horizon` rows, which have no complete forward window -- never
    silently labeled, so a caller can't accidentally grade an incomplete
    window as RANGE)."""
    n = len(df)
    close = df["c"].to_numpy()
    high = df["h"].to_numpy()
    low = df["l"].to_numpy()
    atr = df[atr_col].to_numpy()
    labels = np.full(n, None, dtype=object)

    for i in range(n - horizon):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            labels[i] = "RANGE"   # no valid ATR to define a threshold -- not a directional read, but not
            continue              # "no data" either (data.py guarantees close/high/low exist); documented
        up_target = close[i] + atr_mult * a
        down_target = close[i] - atr_mult * a
        result = "RANGE"
        for j in range(i + 1, i + 1 + horizon):
            hit_up = high[j] >= up_target
            hit_down = low[j] <= down_target
            if hit_up and hit_down:
                result = "RANGE"
                break
            if hit_up:
                result = "UP"
                break
            if hit_down:
                result = "DOWN"
                break
        labels[i] = result
    # dtype=object is explicit and required: pandas' default string-dtype
    # inference (pandas>=2.x "future string" backend) silently turns a
    # `None` entry into NaN once every OTHER entry is a str, which would
    # break the "trailing rows are None, not silently mislabeled" contract.
    return pd.Series(labels, index=df.index, name="label", dtype=object)
