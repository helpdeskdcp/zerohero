"""
Cases 1, 2, 11, 12, 16 from the spec's required test list:
  1. Price above 5 SMA -> bullish condition
  2. Price below 5 SMA -> bearish condition
  11. Missing OI -> degrade confidence, don't crash
  12. Missing volume -> degrade confidence, don't crash
  16. Extreme volatility
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
from app.strategy.scoring import (
    bearish_conditions,
    bullish_conditions,
    weighted_score,
)


def _bar(i, o, h, l, c, v=1000):
    t = (datetime.datetime(2026, 1, 5, 9, 15) + datetime.timedelta(minutes=5 * i)).isoformat()
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def _uptrend_bars(n=40, vol=1000):
    return [_bar(i, 100 + i * 0.8, 100 + i * 0.8 + 0.5, 100 + i * 0.8 - 0.3, 100 + i * 0.8 + 0.3, vol)
            for i in range(n)]


def _downtrend_bars(n=40, vol=1000):
    return [_bar(i, 200 - i * 0.8, 200 - i * 0.8 + 0.3, 200 - i * 0.8 - 0.5, 200 - i * 0.8 - 0.3, vol)
            for i in range(n)]


def test_price_above_5sma_is_a_bullish_condition():
    cfg = StrategyConfig()
    feats = MarketFeatures(bars=_uptrend_bars())
    ind = build_indicator_snapshot(feats, cfg)
    conds = bullish_conditions(ind, feats, cfg)
    assert conds["price_above_5sma"] == ("trend", True)


def test_price_below_5sma_is_a_bearish_condition():
    cfg = StrategyConfig()
    feats = MarketFeatures(bars=_downtrend_bars())
    ind = build_indicator_snapshot(feats, cfg)
    conds = bearish_conditions(ind, feats, cfg)
    assert conds["price_below_5sma"] == ("trend", True)


def test_missing_oi_degrades_score_without_crashing():
    cfg = StrategyConfig()
    feats_with_oi = MarketFeatures(bars=_uptrend_bars(), oi_chg=500.0)
    feats_no_oi = MarketFeatures(bars=_uptrend_bars(), oi_chg=None)
    ind = build_indicator_snapshot(feats_no_oi, cfg)
    conds_no_oi = bullish_conditions(ind, feats_no_oi, cfg)
    assert conds_no_oi["oi_confirms"] == ("oi", None)          # never crashes, never a hard fail
    result_no_oi = weighted_score(conds_no_oi, cfg.normalized_weights("CE"))
    assert result_no_oi["data_completeness"] < 1.0             # completeness genuinely lower
    ind2 = build_indicator_snapshot(feats_with_oi, cfg)
    conds_with_oi = bullish_conditions(ind2, feats_with_oi, cfg)
    result_with_oi = weighted_score(conds_with_oi, cfg.normalized_weights("CE"))
    assert result_with_oi["data_completeness"] > result_no_oi["data_completeness"]


def test_missing_volume_degrades_score_without_crashing():
    cfg = StrategyConfig()
    feats = MarketFeatures(bars=_uptrend_bars(vol=0))    # zero volume everywhere -> avg_volume is 0/None
    ind = build_indicator_snapshot(feats, cfg)
    assert ind is not None
    conds = bullish_conditions(ind, feats, cfg)
    assert conds["volume_confirms"] == ("volume", None)
    result = weighted_score(conds, cfg.normalized_weights("CE"))
    assert result["score"] >= 0.0    # did not crash / did not produce a nonsensical negative score


def test_extreme_volatility_fails_the_volatility_condition():
    cfg = StrategyConfig(volatility_max_atr_pct=1.0)
    # wide-ranging, choppy bars -> large ATR relative to price
    bars = []
    price = 100.0
    for i in range(40):
        price += 5 if i % 2 == 0 else -4.5
        bars.append(_bar(i, price, price + 6, price - 6, price, 1000))
    feats = MarketFeatures(bars=bars)
    ind = build_indicator_snapshot(feats, cfg)
    conds = bullish_conditions(ind, feats, cfg)
    assert conds["volatility_ok"] == ("volatility", False)
