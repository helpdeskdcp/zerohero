"""
app/liquidity_sweep/engine.py -- end-to-end orchestration integration tests.
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.liquidity_sweep import engine  # noqa: E402


def _ts(i):
    base = datetime.datetime(2026, 9, 3, 9, 15)
    return (base + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bar(i, o, h, l, c, v=2000):
    return {"t": _ts(i), "o": o, "h": h, "l": l, "c": c, "v": v}


def _full_bullish_scenario():
    """A hand-verified 35-bar 5m series: an equal-low liquidity pool forms
    twice around ~99.5-99.6, price sweeps below it (wick to 97), then the
    very next bar reclaims hard AND breaks the prior swing-high structure --
    a genuine LOWER_SWEEP + bullish CHoCH + CISD/OrderBlock confirmation."""
    bars = [_bar(i, 100.0, 100.4, 99.7, 100.1, 1500) for i in range(10)]
    pattern = [100, 101, 99.6, 100.5, 102.8, 101.5, 99.55, 100.2, 102.9, 101.0,
              100.3, 99.7, 101.8, 100.9, 99.6, 100.4]
    off = len(bars)
    bars += [_bar(off + i, p, p + 0.6, p - 0.6, p + 0.1, 2000) for i, p in enumerate(pattern)]
    off = len(bars)
    bars += [_bar(off + i, 100.3, 100.9, 99.8, 100.3, 2000) for i in range(6)]
    n0 = len(bars)
    bars.append(_bar(n0, 100.2, 100.5, 99.8, 100.0, 2000))
    bars.append(_bar(n0 + 1, 100.0, 100.3, 97.0, 97.5, 4000))     # sweep wick, not yet reclaimed
    bars.append(_bar(n0 + 2, 97.5, 108.0, 97.3, 107.5, 9000))     # reclaim + structure break bar
    return bars


def test_full_bullish_scenario_produces_a_valid_buy_ce_signal():
    bars = _full_bullish_scenario()
    out = engine.evaluate(symbol="NIFTY", bars_by_tf={"5m": bars}, spot=bars[-1]["c"])
    assert out["decision"] == "BUY_CE"
    assert out["liquidity_sweep"]["kind"] == "LOWER_SWEEP"
    assert out["structure"]["type"] in ("CHOCH", "BOS")
    assert out["structure"]["direction"] == "BULLISH"
    assert out["confirmation"]["candle_closed"] is True
    assert out["rr"] >= 2.0
    assert out["probability"]["up"] > out["probability"]["down"]
    assert 0 <= out["confidence"] <= 100
    assert out["option"]["status"] == "NOT_EVALUATED"   # no option_candidates supplied (Stage 1)
    assert out["failed_rules"] == []


def test_no_option_candidates_never_fabricates_an_option_block():
    bars = _full_bullish_scenario()
    out = engine.evaluate(symbol="NIFTY", bars_by_tf={"5m": bars}, spot=bars[-1]["c"])
    assert "ltp" not in out["option"]
    assert "strike" not in out["option"]


def test_insufficient_history_is_no_trade():
    bars = [_bar(i, 100, 101, 99, 100) for i in range(5)]
    out = engine.evaluate(symbol="NIFTY", bars_by_tf={"5m": bars}, spot=100.0)
    assert out["decision"] == "NO_TRADE"
    assert "insufficient" in out["reason"]


def test_flat_market_with_no_sweep_is_no_trade():
    bars = [_bar(i, 100 + (i % 3) * 0.01, 100.5, 99.5, 100 + (i % 3) * 0.01) for i in range(40)]
    out = engine.evaluate(symbol="NIFTY", bars_by_tf={"5m": bars}, spot=100.0)
    assert out["decision"] == "NO_TRADE"


def test_high_probability_threshold_forces_no_trade_even_with_a_confirmed_setup():
    bars = _full_bullish_scenario()
    out = engine.evaluate(symbol="NIFTY", bars_by_tf={"5m": bars}, spot=bars[-1]["c"],
                          config={"min_probability": 0.99})
    assert out["decision"] == "NO_TRADE"
    assert "probability" in out["reason"]


def test_option_candidates_are_evaluated_when_supplied():
    bars = _full_bullish_scenario()
    spot = bars[-1]["c"]
    candidates = {"CE": [{"strike": spot - 5, "delta": 0.65, "ltp": 20.0, "bid": 19.8, "ask": 20.2,
                          "volume": 5000, "oi": 50000}]}
    out = engine.evaluate(symbol="NIFTY", bars_by_tf={"5m": bars}, spot=spot,
                          option_candidates=candidates, config={"delta_band": (0.6, 0.7)})
    assert out["decision"] == "BUY_CE"
    assert out["option"]["status"] == "OK"
    assert out["option"]["strike"] == spot - 5


def test_no_eligible_strike_is_no_trade_even_with_a_valid_index_signal():
    bars = _full_bullish_scenario()
    spot = bars[-1]["c"]
    candidates = {"CE": [{"strike": spot + 50, "delta": 0.65, "ltp": 20.0, "bid": 19.5, "ask": 20.5,
                          "volume": 5000, "oi": 50000}]}   # NOT itm (strike above spot)
    out = engine.evaluate(symbol="NIFTY", bars_by_tf={"5m": bars}, spot=spot,
                          option_candidates=candidates)
    assert out["decision"] == "NO_TRADE"
    assert "strike" in out["reason"]
