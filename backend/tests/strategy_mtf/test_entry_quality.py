"""
Entry Quality Filter: 5-SMA entry hard rule, anti-chase/LATE_ENTRY,
minimum-target-size, pullback health classification.
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from app.strategy.base_strategy import (
    MarketFeatures,
    build_indicator_snapshot,
)
from app.strategy.config import StrategyConfig
from app.strategy_mtf.entry_quality import (
    HEALTHY_PULLBACK,
    STRUCTURAL_REVERSAL,
    evaluate,
)
from app.strategy_mtf.mtf_config import MTFConfig


def _bar(i, c, v=1000):
    t = (datetime.datetime(2026, 1, 5, 9, 15) + datetime.timedelta(minutes=5 * i)).isoformat()
    return {"t": t, "o": c - 0.1, "h": c + 0.3, "l": c - 0.3, "c": c, "v": v}


def _ind(bars):
    return build_indicator_snapshot(MarketFeatures(bars=bars), StrategyConfig())


def test_price_above_5sma_and_rising_passes_ce_entry_rule():
    bars = [_bar(i, 100 + i * 0.5) for i in range(30)]
    r = evaluate("BULLISH", _ind(bars), cfg=MTFConfig())
    assert r.sma_entry_ok is True


def test_price_below_5sma_fails_ce_entry_rule():
    bars = [_bar(i, 100 - i * 0.5) for i in range(30)]
    r = evaluate("BULLISH", _ind(bars), cfg=MTFConfig())
    assert r.sma_entry_ok is False
    assert r.passed is False


def test_a_move_that_already_ran_far_is_flagged_late_entry():
    cfg = MTFConfig(late_entry_atr_mult=1.0)
    bars = [_bar(i, 100, v=1000) for i in range(24)] + [_bar(24 + i, 100 + i * 5, v=1000) for i in range(6)]
    r = evaluate("BULLISH", _ind(bars), cfg=cfg)
    assert r.late_entry is True
    assert r.passed is False


def test_target_below_minimum_points_fails():
    bars = [_bar(i, 100 + i * 0.5) for i in range(30)]
    cfg = MTFConfig(min_target_points=10.0)
    r = evaluate("BULLISH", _ind(bars), entry=100.0, stop_loss=98.0, cfg=cfg)   # only 2 points of risk
    assert r.target_size_ok is False
    assert r.passed is False


def test_shallow_pullback_reads_as_healthy():
    up = [_bar(i, 100 + i * 1.0) for i in range(25)]
    small_dip = [_bar(25 + i, up[-1]["c"] - i * 0.3) for i in range(3)]   # a shallow dip
    r = evaluate("BULLISH", _ind(up + small_dip), cfg=MTFConfig(pullback_healthy_max_atr_mult=3.0))
    assert r.pullback_status in (HEALTHY_PULLBACK, "NO_PULLBACK")


def test_deep_pullback_reads_as_structural_reversal():
    up = [_bar(i, 100 + i * 1.0) for i in range(25)]
    deep_dip = [_bar(25 + i, up[-1]["c"] - i * 3.0) for i in range(5)]   # a violent retracement
    r = evaluate("BULLISH", _ind(up + deep_dip), cfg=MTFConfig(pullback_healthy_max_atr_mult=0.5))
    assert r.pullback_status == STRUCTURAL_REVERSAL
    assert r.passed is False
