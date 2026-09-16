"""
Cases 2, 4, 6 from the spec's required test list -- the bearish mirror of
test_ce_strategy.py.
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from app.strategy.base_strategy import MarketFeatures  # noqa: E402
from app.strategy.config import StrategyConfig  # noqa: E402
from app.strategy.pe_strategy import evaluate_pe  # noqa: E402


def _bar(i, o, h, l, c, v=1000):
    t = (datetime.datetime(2026, 1, 5, 9, 15) + datetime.timedelta(minutes=5 * i)).isoformat()
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def test_clean_downtrend_produces_a_valid_pe_signal_above_threshold():
    cfg = StrategyConfig()
    bars = [_bar(i, 200 - i * 0.8, 200 - i * 0.8 + 0.3, 200 - i * 0.8 - 0.5, 200 - i * 0.8 - 0.3,
                1000 + i * 50) for i in range(40)]
    feats = MarketFeatures(bars=bars, oi_chg=500.0)
    r = evaluate_pe(feats, cfg)
    assert r.score >= cfg.minimum_threshold
    assert r.valid is True
    assert "price_below_5sma" in r.conditions_passed


def test_bearish_5_20_sma_crossover_is_captured():
    cfg = StrategyConfig()
    flat = [_bar(i, 100, 100.1, 99.9, 100.0, 1000) for i in range(20)]
    fall = [_bar(20 + i, 100 - i * 1.2, 100 - i * 1.2 + 0.2, 100 - i * 1.2 - 0.3, 100 - i * 1.2 - 0.2, 1200)
           for i in range(10)]
    feats = MarketFeatures(bars=flat + fall)
    r = evaluate_pe(feats, cfg)
    assert "sma5_below_sma20" in r.conditions_passed


def test_price_clearly_above_5sma_cannot_produce_a_valid_pe_signal():
    cfg = StrategyConfig()
    bars = [_bar(i, 200 - i * 0.5, 200 - i * 0.5 + 0.2, 200 - i * 0.5 - 0.3, 200 - i * 0.5 - 0.2, 1000)
           for i in range(35)]
    bars.append(_bar(35, 182.5, 192.0, 182.4, 191.5, 1000))   # sharp spike up on the last bar
    feats = MarketFeatures(bars=bars)
    r = evaluate_pe(feats, cfg)
    assert r.valid is False
    assert "price_below_5sma" in r.conditions_failed
