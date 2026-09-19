"""
app/index_signal_research/hypotheses.py -- each hypothesis fires exactly
where its documented condition holds, using constructed feature frames
(bypassing the full features.py pipeline for clarity/speed -- this tests
the hypothesis LOGIC, not feature computation, which features.py's own
tests already cover).
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.index_signal_research.hypotheses import (  # noqa: E402
    HYPOTHESES, hyp_a_trend_continuation, hyp_b_breakout, hyp_c_mean_reversion,
    hyp_d_pullback_in_trend, hyp_e_opening_range, hyp_f_volatility_expansion, hyp_g_mtf_structure,
)


def _base_frame(n=5, **overrides):
    df = pd.DataFrame({
        "expansion_move_atr": [0.0] * n, "struct_uptrend": [False] * n, "struct_downtrend": [False] * n,
        "breakout_up_atr": [0.0] * n, "breakout_down_atr": [0.0] * n,
        "dist_from_ema20_atr": [0.0] * n, "rsi14": [50.0] * n, "ret_1": [0.0] * n,
        "vol_expansion_ratio": [1.0] * n, "c": [100.0] * n,
    })
    for k, v in overrides.items():
        df[k] = v
    return df


def test_hyp_a_fires_only_with_both_expansion_and_structure_agreement():
    df = _base_frame(3, expansion_move_atr=[1.5, 1.5, -0.2], struct_uptrend=[True, False, False])
    out = hyp_a_trend_continuation(df)
    assert out.iloc[0] == "UP"
    assert pd.isna(out.iloc[1])    # expansion present but structure disagrees
    assert pd.isna(out.iloc[2])


def test_hyp_b_breakout_direction_matches_breakout_side():
    df = _base_frame(2, breakout_up_atr=[0.5, 0.0], breakout_down_atr=[0.0, 0.5])
    out = hyp_b_breakout(df)
    assert out.iloc[0] == "UP" and out.iloc[1] == "DOWN"


def test_hyp_c_reversion_vs_continuation_are_opposite_mappings():
    df = _base_frame(1, dist_from_ema20_atr=[3.0], rsi14=[80.0])   # extreme high + overbought
    reversion = hyp_c_mean_reversion(df, bet="reversion")
    continuation = hyp_c_mean_reversion(df, bet="continuation")
    assert reversion.iloc[0] == "DOWN"
    assert continuation.iloc[0] == "UP"


def test_hyp_d_requires_trend_pullback_and_confirming_candle_color():
    df = _base_frame(2, struct_uptrend=[True, True], dist_from_ema20_atr=[0.2, 0.2], ret_1=[0.01, -0.01])
    out = hyp_d_pullback_in_trend(df)
    assert out.iloc[0] == "UP"     # uptrend + near ema + bullish candle
    assert pd.isna(out.iloc[1])     # uptrend + near ema but BEARISH candle -- no confirmation


def test_hyp_e_opening_range_only_fires_after_the_opening_window_and_on_breakout():
    ts = pd.date_range("2026-01-05T03:45:00Z", periods=10, freq="5min", tz="UTC")
    highs = [100, 100, 100, 100, 100, 100, 106, 100, 100, 100]   # OR = first 6 bars, high=100
    lows = [99] * 10
    closes = [100, 100, 100, 100, 100, 100, 106, 100, 100, 100]
    df = pd.DataFrame({"t": ts, "h": highs, "l": lows, "c": closes})
    df["session_date"] = "2026-01-05"
    df["minutes_since_open"] = [i * 5 for i in range(10)]
    out = hyp_e_opening_range(df)
    assert out.iloc[:6].isna().all()     # nothing can fire during the opening range itself
    assert out.iloc[6] == "UP"           # breaks above OR high right after it completes


def test_hyp_f_direction_is_the_bar_own_return_sign_only_when_vol_has_expanded():
    df = _base_frame(2, vol_expansion_ratio=[2.0, 1.0], ret_1=[0.01, 0.01])
    out = hyp_f_volatility_expansion(df)
    assert out.iloc[0] == "UP"
    assert pd.isna(out.iloc[1])    # same positive return, but vol never expanded -- must not fire


def test_hyp_g_requires_htf_agreement_and_5m_confirmation_together():
    df = _base_frame(2, struct_uptrend=[True, True], dist_from_ema20_atr=[0.2, 0.2], ret_1=[0.01, 0.01])
    df["htf_regime"] = ["BULLISH", "BEARISH"]
    out = hyp_g_mtf_structure(df)
    assert out.iloc[0] == "UP"     # HTF bullish + 5m confirms up
    assert pd.isna(out.iloc[1])     # 5m confirms up but HTF disagrees (bearish) -- must not fire


def test_all_registered_hypotheses_are_callable_and_return_a_series():
    df = _base_frame(3)
    df["htf_regime"] = ["RANGE"] * 3
    df["minutes_since_open"] = [10, 100, 200]
    df["session_date"] = ["2026-01-05"] * 3
    df["h"] = [100.3] * 3
    df["l"] = [99.7] * 3
    for name, fn in HYPOTHESES.items():
        out = fn(df)
        assert isinstance(out, pd.Series), f"{name} did not return a Series"
        assert len(out) == 3
