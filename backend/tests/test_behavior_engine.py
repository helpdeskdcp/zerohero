"""app.behavior_engine -- pure interpretation of an already-computed
decide_from_context() signal dict. No indicator math, no I/O."""
from app.behavior_engine import analyze_behavior


def _sig(**over):
    base = {"regime": "TRENDING_UP", "signal_type": "RESISTANCE_BREAKOUT", "direction": "BULLISH",
           "mtf_alignment": 55.0, "signal_score": 72.0, "atr": 10.0, "entry": 500.0}
    base.update(over)
    return base


def test_breakout_signal_type_maps_to_breakout_regime():
    b = analyze_behavior("NIFTY", _sig(), "UNCALIBRATED")
    assert b.regime == "BREAKOUT"


def test_reversal_signal_type_maps_to_reversal_regime():
    b = analyze_behavior("NIFTY", _sig(signal_type="SUPPORT_REVERSAL"), "UNCALIBRATED")
    assert b.regime == "REVERSAL"


def test_trending_regime_with_no_specific_signal_type_maps_to_trend():
    b = analyze_behavior("NIFTY", _sig(signal_type="NONE", regime="TRENDING_DOWN"), "UNCALIBRATED")
    assert b.regime == "TREND"
    assert b.trend == "DOWN"


def test_range_regime_maps_to_range():
    b = analyze_behavior("NIFTY", _sig(signal_type="NONE", regime="RANGE"), "UNCALIBRATED")
    assert b.regime == "RANGE"


def test_unknown_regime_maps_to_chop():
    b = analyze_behavior("NIFTY", _sig(signal_type="NONE", regime="UNKNOWN"), "UNCALIBRATED")
    assert b.regime == "CHOP"


def test_volatility_classification_from_atr_pct():
    high = analyze_behavior("NIFTY", _sig(atr=20.0, entry=500.0), "UNCALIBRATED")   # 4%
    low = analyze_behavior("NIFTY", _sig(atr=1.0, entry=500.0), "UNCALIBRATED")     # 0.2%
    normal = analyze_behavior("NIFTY", _sig(atr=5.0, entry=500.0), "UNCALIBRATED")  # 1%
    assert high.volatility == "HIGH"
    assert low.volatility == "LOW"
    assert normal.volatility == "NORMAL"


def test_missing_atr_is_unknown_not_guessed():
    b = analyze_behavior("NIFTY", _sig(atr=None), "UNCALIBRATED")
    assert b.volatility == "UNKNOWN"


def test_confidence_and_quality_clamped_0_100():
    b = analyze_behavior("NIFTY", _sig(mtf_alignment=-999.0, signal_score=999.0), "UNCALIBRATED")
    assert b.behavior_confidence == 100.0
    assert b.signal_quality == 100.0


def test_risk_state_reflects_uncalibrated_cost():
    b = analyze_behavior("NIFTY", _sig(), "UNCALIBRATED")
    assert b.risk_state == "ELEVATED_UNCALIBRATED_COST"


def test_risk_state_normal_when_calibrated_and_not_volatile():
    b = analyze_behavior("NATURALGAS", _sig(atr=1.0, entry=500.0), "OK")
    assert b.risk_state == "NORMAL"


def test_ai_required_flag_for_ambiguous_confidence():
    b = analyze_behavior("NIFTY", _sig(mtf_alignment=50.0, signal_type="NONE", regime="TRENDING_UP"),
                         "OK")
    assert b.ai_required is True


def test_market_state_composite_string():
    b = analyze_behavior("NIFTY", _sig(), "UNCALIBRATED")
    assert b.market_state == f"{b.regime}_{b.trend}"


def test_to_dict_roundtrip():
    b = analyze_behavior("BANKNIFTY", _sig(), "UNCALIBRATED")
    d = b.to_dict()
    assert d["symbol"] == "BANKNIFTY" and d["profile"] == "BANKNIFTY"
    assert "ai_required" in d
