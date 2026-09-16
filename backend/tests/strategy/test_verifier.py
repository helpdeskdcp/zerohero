"""
Cases 10, 14, 15, 16, 17, 18 from the spec's required test list:
  10. Insufficient data -> NO_TRADE
  14. Signal persistence
  15. Support/resistance conflict
  16. Extreme volatility (end-to-end through the verifier)
  17. Sideways market
  18. Strategy disabled/configuration changes
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from app.strategy.base_strategy import MarketFeatures  # noqa: E402
from app.strategy.config import StrategyConfig  # noqa: E402
from app.strategy.strategy_verifier import StrategyVerifier  # noqa: E402


def _bar(i, o, h, l, c, v=1000):
    t = (datetime.datetime(2026, 1, 5, 9, 15) + datetime.timedelta(minutes=5 * i)).isoformat()
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def _uptrend(n=40, vol=1000):
    return [_bar(i, 100 + i * 0.8, 100 + i * 0.8 + 0.5, 100 + i * 0.8 - 0.3, 100 + i * 0.8 + 0.3, vol)
            for i in range(n)]


def test_insufficient_data_is_no_trade():
    v = StrategyVerifier(StrategyConfig())
    feats = MarketFeatures(bars=[_bar(0, 100, 101, 99, 100)])
    r = v.verify(symbol="NIFTY", raw_signal="BUY", features=feats, timestamp="2026-01-05T09:15:00")
    assert r.strategy_direction == "NO_TRADE"
    assert r.entry_allowed is False


def test_signal_must_persist_before_entry_ready():
    v = StrategyVerifier(StrategyConfig(confirmation_min_count=3))
    states = []
    for extra in range(4):
        bars = _uptrend(n=40 + extra, vol=1000 + extra * 50)
        feats = MarketFeatures(bars=bars, oi_chg=500.0)
        r = v.verify(symbol="NIFTY", raw_signal="BUY", features=feats, timestamp=bars[-1]["t"])
        states.append(r.state)
    # must NOT jump straight to ENTRY_READY on the very first read
    assert states[0] != "ENTRY_READY"
    # but must eventually reach it once persistence requirements are met
    assert "ENTRY_READY" in states
    assert states.index("ENTRY_READY") >= 2   # took at least 3 consecutive reads (min_count=3)


def test_support_resistance_conflict_blocks_confirmation():
    """Price sitting right on a known level, with no persistence yet, must
    be held back -- a breakout/rejection has to be CONFIRMED, not assumed
    from a single read next to the level."""
    v = StrategyVerifier(StrategyConfig())
    bars = _uptrend(n=40)
    spot = bars[-1]["c"]
    feats = MarketFeatures(bars=bars, oi_chg=500.0, resistance=spot + 0.1)   # essentially AT resistance
    r = v.verify(symbol="NIFTY", raw_signal="BUY", features=feats, timestamp=bars[-1]["t"])
    assert r.entry_allowed is False
    assert "near_sr_without_confirmed_break" in r.confirmation_blockers


def test_extreme_volatility_reduces_score_end_to_end():
    cfg = StrategyConfig(volatility_max_atr_pct=1.0)
    v = StrategyVerifier(cfg)
    bars = []
    price = 100.0
    for i in range(40):
        price += 6 if i % 2 == 0 else -5.5
        bars.append(_bar(i, price, price + 7, price - 7, price, 1000))
    feats = MarketFeatures(bars=bars)
    r = v.verify(symbol="NIFTY", raw_signal="BUY", features=feats, timestamp=bars[-1]["t"])
    assert "volatility_ok" not in r.conditions_passed


def test_sideways_market_is_held_back_from_entry():
    v = StrategyVerifier(StrategyConfig(sideways_adx_max=50.0))   # force ADX to read as "sideways"
    bars = [_bar(i, 100, 100.05, 99.95, 100.0 + (0.01 if i % 2 == 0 else -0.01), 1000) for i in range(40)]
    feats = MarketFeatures(bars=bars, oi_chg=10.0)
    r = v.verify(symbol="NIFTY", raw_signal="BUY", features=feats, timestamp=bars[-1]["t"])
    assert r.entry_allowed is False


def test_disabling_a_bucket_via_config_changes_the_outcome():
    """Configuration changes (spec case 18): zeroing a bucket's weight
    must change the score without crashing anything."""
    bars = _uptrend(n=40, vol=1000)
    feats = MarketFeatures(bars=bars)   # no OI at all
    default_cfg = StrategyConfig()
    v1 = StrategyVerifier(default_cfg)
    r1 = v1.verify(symbol="NIFTY", raw_signal="BUY", features=feats, timestamp=bars[-1]["t"])

    heavy_oi_cfg = StrategyConfig(ce_weights={**default_cfg.ce_weights, "oi": 0.50, "trend": 0.05})
    v2 = StrategyVerifier(heavy_oi_cfg)
    r2 = v2.verify(symbol="NIFTY", raw_signal="BUY", features=feats, timestamp=bars[-1]["t"])
    assert r1.ce_score != r2.ce_score or r1.confidence != r2.confidence


def test_extremely_high_threshold_config_forces_no_trade():
    v = StrategyVerifier(StrategyConfig(minimum_threshold=99.9))
    bars = _uptrend(n=40)
    feats = MarketFeatures(bars=bars, oi_chg=500.0)
    r = v.verify(symbol="NIFTY", raw_signal="BUY", features=feats, timestamp=bars[-1]["t"])
    assert r.strategy_direction == "NO_TRADE"
