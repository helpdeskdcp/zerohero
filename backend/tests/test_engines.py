"""Deterministic engine behaviour — signal / scalp / risk / reversal / OI."""
import time

import pytest

from conftest import candles

from app.engines.signal_engine import run_signal_engine
from app.engines.scalp_engine import run_scalp_engine
from app.engines.risk_engine import run_risk_engine
from app.engines.oi_options_engine import run_oi_options_engine
from app.reversal import detect_reversal


# ---------------------------------------------------------------- signal_engine
def test_signal_insufficient_candles():
    out = run_signal_engine({"candles": candles([100, 101, 102]), "config": {}})
    assert out["decision"] == "DATA_UNAVAILABLE"


def test_signal_flat_market_is_no_trade():
    out = run_signal_engine({"candles": candles([100.0] * 60), "config": {}})
    assert out["decision"] == "NO_TRADE"
    assert any("flat" in r.lower() for r in out["reason"])


def test_signal_is_deterministic():
    px = [100 + i * 0.1 for i in range(60)]
    a = run_signal_engine({"candles": candles(px), "config": {}})
    b = run_signal_engine({"candles": candles(px), "config": {}})
    assert a["decision"] == b["decision"]
    assert a["probability"] == b["probability"]
    assert a["risk_reward"] == b["risk_reward"]


def test_signal_no_trade_hides_probability_but_keeps_lean():
    # Clean uptrend: every evidence check bullish (ev ~ 5.4) so the raw
    # sigmoid-of-evidence saturates near 99.6 -- but default RR (1.0/1.2 ATR)
    # is 0.83 < rr_min 1.5, so the engine returns NO_TRADE.
    up = [100 + i * 0.25 for i in range(120)]
    out = run_signal_engine({"candles": candles(up), "config": {}})
    assert out["decision"] == "NO_TRADE"
    assert out["direction"] == "NONE"
    # Fix 1: a rejected signal presents no trade-probability ...
    assert out["probability"] is None
    # ... but the discarded directional read is preserved explicitly
    assert out["direction_lean"] == "BUY"
    assert out["lean_score"] == pytest.approx(99.6, abs=0.5)
    # `confidence` stays as the evidence-magnitude meter (|ev| rescaled to
    # 0-100, clamped) -- it is NOT re-presented as a win probability.
    assert out["confidence"] == pytest.approx(100.0, abs=0.01)
    # risk-gate math is untouched
    assert out["risk_reward"] == 0.83
    assert out["market_regime"] == "TRENDING_UP"


def test_signal_trade_path_probability_unchanged():
    # Loosen rr_min via *config input only* (no code / ATR-multiplier / rr_min
    # source change) so the same uptrend is actionable. The TRADE return must
    # be untouched by Fix 1: `probability` is still the real number.
    up = [100 + i * 0.25 for i in range(120)]
    out = run_signal_engine({"candles": candles(up),
                             "config": {"rr_min": 0.5, "prob_min": 40}})
    assert out["decision"] == "TRADE"
    assert out["direction"] == "BUY"
    assert out["probability"] == pytest.approx(99.6, abs=0.5)


# ---------------------------------------------------------------- scalp_engine
def test_scalp_insufficient_candles():
    out = run_scalp_engine({"candles": candles([100] * 10), "config": {"ignore_session": True}})
    assert out["decision"] == "DATA_UNAVAILABLE"


def test_scalp_momentum_break_fires_buy():
    # gentle uptrend then a decisive breakout bar
    px = [100 + i * 0.03 for i in range(39)]
    cds = candles(px)
    last_t = cds[-1][0] + 60
    prior_hi = max(r[2] for r in cds[-6:-1])
    cds.append([last_t, px[-1], prior_hi + 0.35, px[-1] - 0.02, prior_hi + 0.30, 3200])
    out = run_scalp_engine({"candles": cds, "config": {"ignore_session": True}})
    assert out["decision"] == "TRADE"
    assert out["setup"] == "MOMENTUM_BREAK"
    assert out["direction"] == "BUY"
    assert out["tick_target"] and out["tick_stop"]


def test_scalp_flat_is_no_trade():
    out = run_scalp_engine({"candles": candles([100.0] * 40), "config": {"ignore_session": True}})
    assert out["decision"] == "NO_TRADE"


# ---------------------------------------------------------------- risk_engine
def _risk(**over):
    base = {
        "signal": {"direction": "BUY", "entry_ref": 100.0, "stop_loss": 98.0},
        "account": {"capital": 500000, "risk_pct": 1, "available_margin": 1000000},
        "instrument": {"lot_size": 1}, "state": {}, "limits": {},
    }
    base.update(over)
    return run_risk_engine(base)


def test_risk_approves_a_clean_setup():
    out = _risk()
    assert out["risk_status"] == "APPROVED"
    assert out["allowed_quantity"] > 0


def test_risk_rejects_no_capital():
    out = _risk(account={"risk_pct": 1})
    assert out["risk_status"] == "REJECTED"


def test_risk_rejects_kill_switch():
    out = _risk(limits={"kill_switch": True})
    assert out["risk_status"] == "REJECTED"
    assert any("kill switch" in r.lower() for r in out["reasons"])


def test_risk_rejects_wrong_side_stop():
    out = _risk(signal={"direction": "BUY", "entry_ref": 100.0, "stop_loss": 101.0})
    assert out["risk_status"] == "REJECTED"


# ---------------------------------------------------------------- reversal
def test_reversal_none_when_short():
    out = detect_reversal(candles([100] * 10))
    assert out["reversal"] is None


def test_reversal_bearish_at_resistance():
    # ramp up to a resistance, spike-and-reject on the last bars
    px = [100 + i * 0.15 for i in range(30)]
    cds = candles(px)
    res = max(r[2] for r in cds)
    t = cds[-1][0]
    cds.append([t + 60, px[-1], res + 0.3, px[-1] - 0.1, px[-1] - 0.05, 1500])
    cds.append([t + 120, px[-1] - 0.05, res + 0.1, px[-1] - 1.2, px[-1] - 1.1, 1800])
    out = detect_reversal(cds)
    assert out["reversal"] == "BEARISH"
    assert out["option"] == "PE"
    assert out["stop"] > out["entry"] > out["target_1"]


def test_reversal_none_midrange():
    px = [100 + (i % 5) * 0.2 for i in range(40)]
    out = detect_reversal(candles(px))
    assert out["reversal"] is None


# ---------------------------------------------------------------- oi engine
def _chain(spot=100):
    rows = []
    for k in range(spot - 10, spot + 11, 5):
        rows.append({
            "strike": k,
            "ce_ltp": max(0.5, spot - k + 5), "pe_ltp": max(0.5, k - spot + 5),
            "ce_oi": 2000, "pe_oi": 2500, "ce_oi_change": 100, "pe_oi_change": 400,
            "ce_volume": 500, "pe_volume": 600,
            "ce_bid": 4.9, "ce_ask": 5.0, "pe_bid": 4.9, "pe_ask": 5.0,
        })
    return rows


def test_oi_data_unavailable_without_spot():
    out = run_oi_options_engine({"chain": _chain(), "config": {}})
    assert out["decision"] == "DATA_UNAVAILABLE"


def test_oi_thin_chain():
    out = run_oi_options_engine({"spot": 100, "chain": _chain()[:2], "config": {}})
    assert out["decision"] == "DATA_UNAVAILABLE"


def test_oi_with_bias_returns_a_leg():
    out = run_oi_options_engine({"spot": 100, "chain": _chain(), "directional_bias": "BUY", "config": {}})
    assert out["decision"] in ("TRADE", "NO_TRADE")
    if out["decision"] == "TRADE":
        assert out["option_type"] == "CE"
        assert out["recommended_strike"] is not None
