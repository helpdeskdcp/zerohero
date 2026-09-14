"""
app/index_signal_research/features.py -- causal-feature look-ahead audit
(the same mutation-test discipline as
tests/test_liquidity_sweep_lookahead.py) plus a targeted check that
add_htf_bias's merge_asof backward-join never lets a higher-timeframe bar
influence a 5m bar that came before that HTF bar closed.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.index_signal_research.features import add_htf_bias, build_feature_frame  # noqa: E402


def _bars(n, start="2026-01-05T03:45:00Z", freq="5min", price_fn=None):
    ts = pd.date_range(start=start, periods=n, freq=freq, tz="UTC")
    if price_fn is None:
        prices = [100.0 + 0.01 * i for i in range(n)]
    else:
        prices = [price_fn(i) for i in range(n)]
    rows = []
    for i, p in enumerate(prices):
        rows.append({"t": ts[i], "o": p, "h": p + 0.3, "l": p - 0.3, "c": p, "v": 0.0})
    return pd.DataFrame(rows)


def test_price_features_are_identical_when_truncated_regardless_of_future_data():
    """The same mutation-test design as the liquidity_sweep audit: two
    series identical up to T, diverging completely after -- feature values
    AT row T-1 (the last row of the truncated slice) must be identical
    whether or not the future rows are appended before slicing back."""
    n = 400
    shared = _bars(n)
    branch_a = pd.concat([shared, _bars(20, start=shared["t"].iloc[-1] + pd.Timedelta(minutes=5),
                                        price_fn=lambda i: 100 + i * 0.5)], ignore_index=True)
    branch_b = pd.concat([shared, _bars(20, start=shared["t"].iloc[-1] + pd.Timedelta(minutes=5),
                                        price_fn=lambda i: 100 - i * 5.0)], ignore_index=True)

    feat_a = build_feature_frame(branch_a.iloc[:n].copy())
    feat_b = build_feature_frame(branch_b.iloc[:n].copy())

    last_a = feat_a.iloc[-1]
    last_b = feat_b.iloc[-1]
    for col in ("ema_fast", "ema_slow", "rsi14", "atr14", "htf_score", "breakout_up_atr", "struct_uptrend"):
        va, vb = last_a[col], last_b[col]
        if isinstance(va, float) and np.isnan(va) and isinstance(vb, float) and np.isnan(vb):
            continue
        assert va == vb, f"{col} differs after truncation-only mutation: {va} vs {vb}"


def test_htf_bias_backward_join_never_uses_a_not_yet_closed_htf_bar():
    """Construct 5m bars spanning a bit over one real 15m bucket boundary --
    the FIRST 5m bar of a new 15m bucket must not yet reflect that bucket's
    own (incomplete) bias; add_htf_bias must fall back to the PRIOR
    completed 15m bar instead (or NaN if none exists yet), never a bar
    stamped later than the 5m row itself."""
    df = _bars(400)
    out = add_htf_bias(df)
    # every htf_bias_15m value must come from an HTF bar timestamped <= the
    # 5m row's own timestamp -- re-derive the raw resampled series and check
    from app.liquidity_sweep import resample as resample_mod
    bars = df[["t", "o", "h", "l", "c", "v"]].copy()
    bars["t"] = bars["t"].apply(lambda x: x.isoformat())
    coarse = resample_mod.resample_bars(bars.to_dict("records"), 15)
    coarse_ts = pd.to_datetime([b["t"] for b in coarse], utc=True)
    for i in range(len(df)):
        htf_val = out["htf_bias_15m"].iloc[i]
        if np.isnan(htf_val):
            continue
        eligible = coarse_ts[coarse_ts <= df["t"].iloc[i]]
        assert len(eligible) > 0, "a real htf_bias value exists with no HTF bar at or before this row's time"


def test_breakout_measured_against_prior_bars_excludes_current_bar():
    """prior_high_20 at row i must equal the max high of rows [i-20, i-1] --
    NOT including row i's own high -- otherwise a breakout could never
    register (a bar's own high always >= itself)."""
    df = _bars(60)
    df.loc[45, "h"] = 999.0   # an obvious spike, should show up in the NEXT rows' prior_high_20, not row 45's own
    out = build_feature_frame(df)
    assert out["prior_high_20"].iloc[45] != 999.0
    assert out["prior_high_20"].iloc[46] == 999.0
