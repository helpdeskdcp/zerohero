"""PHASE 3 + 7 — snapshot data-quality contract + NO_TRADE reason classifier."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from app.autoscalp.data_quality import snapshot_data_quality, classify_no_trade_reason


def _chain(oi=True, greeks="BROKER"):
    def leg(d):
        return {"oi": 10000 if oi else None, "oi_chg": 100 if oi else None,
                "vol_delta": 5, "greeks_source": greeks,
                "delta": d if greeks == "BROKER" else None, "gamma": 0.001 if greeks == "BROKER" else None}
    return [{"strike": k, "ce": leg(0.5), "pe": leg(-0.5)} for k in (100, 105, 110, 115, 120)]


def test_available_when_everything_present():
    sig = {"index_ltp": 24000, "atr": 20, "vwap": 24010, "vwap_status": "available",
           "momentum": 0.3, "support": 23900, "resistance": 24100, "mtf_alignment": 30}
    dq = snapshot_data_quality(sig, _chain(), {"quality_status": "GOOD"}, feed_age_sec=1.0)
    g = dq["groups"]
    assert g["PRICE"] == "AVAILABLE" and g["VWAP"] == "AVAILABLE" and g["ATR"] == "AVAILABLE"
    assert g["OI"] == "AVAILABLE" and g["PCR"] == "AVAILABLE"
    assert g["DELTA"] == "AVAILABLE" and dq["greeks_source"] == "BROKER"
    assert g["RSI"] == "DERIVED"
    assert dq["score"] > 0.8


def test_mcx_greeks_unsupported_not_missing():
    sig = {"index_ltp": 280, "atr": 1, "vwap": 281, "vwap_status": "available",
           "momentum": -0.2, "support": 278, "resistance": 284, "mtf_alignment": -10}
    dq = snapshot_data_quality(sig, _chain(greeks="UNAVAILABLE"), {"quality_status": "GOOD"},
                               feed_age_sec=1.0, greeks_capability="UNAVAILABLE")
    for grp in ("IV", "DELTA", "GAMMA", "THETA", "VEGA"):
        assert dq["groups"][grp] == "UNSUPPORTED"
    assert dq["greeks_source"] == "UNAVAILABLE"
    # UNSUPPORTED groups excluded from the score -> OI/PCR still carry it
    assert dq["score"] is not None and dq["score"] > 0.7


def test_insufficient_oi_marks_pcr_missing_not_zero():
    dq = snapshot_data_quality({"index_ltp": 1, "atr": 1}, _chain(oi=False),
                               {"quality_status": "INSUFFICIENT_OI"}, feed_age_sec=1.0)
    assert dq["groups"]["OI"] == "MISSING" and dq["groups"]["PCR"] == "MISSING"


def test_stale_feed_downgrades_price_groups():
    sig = {"index_ltp": 24000, "atr": 20, "momentum": 0.3}
    dq = snapshot_data_quality(sig, _chain(), {"quality_status": "GOOD"}, feed_age_sec=45.0)
    assert dq["groups"]["PRICE"] == "STALE" and dq["groups"]["MOMENTUM"] == "STALE"


def test_no_trade_reason_classes():
    assert classify_no_trade_reason({"decision": "BUY_CE"}) == "OK"
    assert classify_no_trade_reason({"decision": "NO_TRADE", "reason": "S/R unavailable"}) == "DATA_UNAVAILABLE"
    assert classify_no_trade_reason({"decision": "NO_TRADE", "regime": "MARKET_CLOSED", "reason": "nse closed"}) == "DATA_UNAVAILABLE"
    assert classify_no_trade_reason({"decision": "NO_TRADE", "reason": "CE/PE confirmation CONFLICT"}) == "CONFLICTING_SIGNAL"
    assert classify_no_trade_reason({"decision": "NO_TRADE", "reason": "no clean state"}) == "MARKET_NEUTRAL"
    assert classify_no_trade_reason({"decision": "NO_TRADE", "reason": "filter: regime UNSTABLE blocked"}) == "FILTER"
    assert classify_no_trade_reason({"decision": "NO_TRADE", "reason": "EV gate: EV 0.05R < 0.12R"}) == "GATE"
