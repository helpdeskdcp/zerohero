"""
Cases 1, 3, 5 from the spec's required test list:
  1. Price above 5 SMA -> bullish condition (via the full CE strategy)
  3. Bullish 5/20 SMA crossover
  5. CE score above threshold -> valid
Plus the explicit "do not generate a strong CE signal while price is
clearly below 5 SMA" hard rule.
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from app.strategy.base_strategy import MarketFeatures  # noqa: E402
from app.strategy.ce_strategy import evaluate_ce  # noqa: E402
from app.strategy.config import StrategyConfig  # noqa: E402


def _bar(i, o, h, l, c, v=1000):
    t = (datetime.datetime(2026, 1, 5, 9, 15) + datetime.timedelta(minutes=5 * i)).isoformat()
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def test_clean_uptrend_produces_a_valid_ce_signal_above_threshold():
    cfg = StrategyConfig()
    bars = [_bar(i, 100 + i * 0.8, 100 + i * 0.8 + 0.5, 100 + i * 0.8 - 0.3, 100 + i * 0.8 + 0.3,
                1000 + i * 50) for i in range(40)]
    feats = MarketFeatures(bars=bars, oi_chg=500.0)
    r = evaluate_ce(feats, cfg)
    assert r.score >= cfg.minimum_threshold
    assert r.valid is True
    assert "price_above_5sma" in r.conditions_passed


def test_bullish_5_20_sma_crossover_is_captured():
    cfg = StrategyConfig()
    # flat-then-rising: 5 SMA crosses above a still-catching-up 20 SMA
    flat = [_bar(i, 100, 100.1, 99.9, 100.0, 1000) for i in range(20)]
    rise = [_bar(20 + i, 100 + i * 1.2, 100 + i * 1.2 + 0.3, 100 + i * 1.2 - 0.2, 100 + i * 1.2 + 0.2, 1200)
           for i in range(10)]
    feats = MarketFeatures(bars=flat + rise)
    r = evaluate_ce(feats, cfg)
    assert "sma5_above_sma20" in r.conditions_passed


def test_price_clearly_below_5sma_cannot_produce_a_valid_ce_signal():
    """The explicit hard rule: even if other indicators look bullish, being
    on the wrong side of the 5 SMA must block a valid CE."""
    cfg = StrategyConfig()
    # strong recent downside wick pulling price well below its own 5 SMA
    bars = [_bar(i, 100 + i * 0.5, 100 + i * 0.5 + 0.3, 100 + i * 0.5 - 0.2, 100 + i * 0.5 + 0.2, 1000)
           for i in range(35)]
    bars.append(_bar(35, 117.5, 117.6, 108.0, 108.5, 1000))   # sharp drop on the last bar
    feats = MarketFeatures(bars=bars)
    r = evaluate_ce(feats, cfg)
    assert r.valid is False
    assert "price_above_5sma" in r.conditions_failed


def test_insufficient_bar_history_returns_invalid_not_a_crash():
    cfg = StrategyConfig()
    feats = MarketFeatures(bars=[_bar(0, 100, 101, 99, 100)])
    r = evaluate_ce(feats, cfg)
    assert r.valid is False and r.entry_allowed is False
    assert r.score == 0.0
