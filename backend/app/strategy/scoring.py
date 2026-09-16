"""
Named condition checks (bullish + the bearish mirror) and the weighted-
bucket scorer. Every condition returns True / False / None -- None means
"insufficient data to judge this one," which the scorer excludes from that
bucket's denominator (degrades the score honestly, never crashes, never
counts a missing indicator as a hard fail).

Bucket shape matches the spec's own worked example exactly: trend,
momentum, vwap, volume, oi, price_action, volatility. Each bucket can hold
several of the spec's named "confirmation inputs" (e.g. HTF trend and the
5/20 SMA relationship both live in `trend`) -- the spec's list of ~16
inputs was explicitly "examples," not one-bucket-each.
"""
from __future__ import annotations

from .base_strategy import IndicatorSnapshot, MarketFeatures

# (bucket, condition_name) -> bool|None, built by {bullish,bearish}_conditions()
Conditions = dict[str, tuple[str, bool | None]]


def _candle_bullish(ind: IndicatorSnapshot) -> bool | None:
    if ind.n < 2:
        return None
    return ind.closes[-1] > ind.closes[-3] if ind.n >= 3 else ind.closes[-1] > ind.closes[-2]


def bullish_conditions(ind: IndicatorSnapshot, features: MarketFeatures, cfg) -> Conditions:
    c: Conditions = {}
    price = ind.close

    # -- trend --
    c["price_above_5sma"] = ("trend", None if ind.sma_fast is None else price > ind.sma_fast)
    c["sma5_slope_up"] = ("trend", None if ind.sma_fast_slope is None else ind.sma_fast_slope >= 0)
    c["sma5_above_sma20"] = ("trend", None if (ind.sma_fast is None or ind.sma_slow is None)
                             else ind.sma_fast >= ind.sma_slow)
    if features.htf_bars:
        from .base_strategy import build_indicator_snapshot
        htf = build_indicator_snapshot(MarketFeatures(bars=features.htf_bars), cfg)
        c["htf_trend_bullish"] = ("trend", None if (htf is None or htf.sma_fast is None)
                                  else htf.close > htf.sma_fast)
    else:
        c["htf_trend_bullish"] = ("trend", None)

    # -- momentum --
    c["rsi_bullish"] = ("momentum", None if (ind.rsi is None or ind.rsi_prev is None)
                        else (ind.rsi >= cfg.rsi_bull_min and ind.rsi >= ind.rsi_prev))
    c["macd_bullish"] = ("momentum", None if (ind.macd is None or ind.macd_prev is None)
                        else (ind.macd > 0 and ind.macd >= ind.macd_prev))
    c["adx_trending"] = ("momentum", None if ind.adx is None else ind.adx >= cfg.adx_trend_min)

    # -- vwap --
    c["above_vwap"] = ("vwap", None if ind.vwap is None else price > ind.vwap)

    # -- volume --
    c["volume_confirms"] = ("volume", None if (not ind.volumes or ind.avg_volume in (None, 0))
                            else ind.volumes[-1] > ind.avg_volume)

    # -- oi -- standard price/OI matrix: rising price + rising OI = fresh
    # long buildup (bullish confirm); rising price + falling OI = short
    # covering (weaker, not a confirm here); missing OI -> None, not a fail.
    if features.oi_chg is None:
        c["oi_confirms"] = ("oi", None)
    else:
        price_up = _candle_bullish(ind)
        c["oi_confirms"] = ("oi", None if price_up is None else (features.oi_chg > 0 and price_up))

    # -- price_action -- candle direction, Bollinger position, S/R
    c["candle_bullish"] = ("price_action", _candle_bullish(ind))
    c["bb_position_bullish"] = ("price_action", None if (ind.bb_mid is None) else price >= ind.bb_mid)
    if features.resistance is not None:
        c["sr_confirms"] = ("price_action", price > features.resistance)
    elif features.support is not None:
        c["sr_confirms"] = ("price_action", price > features.support)
    else:
        c["sr_confirms"] = ("price_action", None)

    # -- volatility -- not "too noisy" to trust a directional read
    c["volatility_ok"] = ("volatility", None if ind.atr_pct is None else ind.atr_pct <= cfg.volatility_max_atr_pct)

    return c


def bearish_conditions(ind: IndicatorSnapshot, features: MarketFeatures, cfg) -> Conditions:
    c: Conditions = {}
    price = ind.close

    c["price_below_5sma"] = ("trend", None if ind.sma_fast is None else price < ind.sma_fast)
    c["sma5_slope_down"] = ("trend", None if ind.sma_fast_slope is None else ind.sma_fast_slope <= 0)
    c["sma5_below_sma20"] = ("trend", None if (ind.sma_fast is None or ind.sma_slow is None)
                             else ind.sma_fast <= ind.sma_slow)
    if features.htf_bars:
        from .base_strategy import build_indicator_snapshot
        htf = build_indicator_snapshot(MarketFeatures(bars=features.htf_bars), cfg)
        c["htf_trend_bearish"] = ("trend", None if (htf is None or htf.sma_fast is None)
                                  else htf.close < htf.sma_fast)
    else:
        c["htf_trend_bearish"] = ("trend", None)

    c["rsi_bearish"] = ("momentum", None if (ind.rsi is None or ind.rsi_prev is None)
                       else (ind.rsi <= cfg.rsi_bear_max and ind.rsi <= ind.rsi_prev))
    c["macd_bearish"] = ("momentum", None if (ind.macd is None or ind.macd_prev is None)
                        else (ind.macd < 0 and ind.macd <= ind.macd_prev))
    c["adx_trending"] = ("momentum", None if ind.adx is None else ind.adx >= cfg.adx_trend_min)

    c["below_vwap"] = ("vwap", None if ind.vwap is None else price < ind.vwap)

    c["volume_confirms"] = ("volume", None if (not ind.volumes or ind.avg_volume in (None, 0))
                            else ind.volumes[-1] > ind.avg_volume)

    if features.oi_chg is None:
        c["oi_confirms"] = ("oi", None)
    else:
        price_down = None if ind.n < 2 else not _candle_bullish(ind)
        c["oi_confirms"] = ("oi", None if price_down is None else (features.oi_chg > 0 and price_down))

    price_down_candle = None if ind.n < 2 else not _candle_bullish(ind)
    c["candle_bearish"] = ("price_action", price_down_candle)
    c["bb_position_bearish"] = ("price_action", None if (ind.bb_mid is None) else price <= ind.bb_mid)
    if features.support is not None:
        c["sr_confirms"] = ("price_action", price < features.support)
    elif features.resistance is not None:
        c["sr_confirms"] = ("price_action", price < features.resistance)
    else:
        c["sr_confirms"] = ("price_action", None)

    c["volatility_ok"] = ("volatility", None if ind.atr_pct is None else ind.atr_pct <= cfg.volatility_max_atr_pct)

    return c


def weighted_score(conditions: Conditions, weights: dict) -> dict:
    """Bucket pass-rate = passed / available within that bucket (None
    entries excluded from the denominator -- missing data degrades, never
    hard-fails). Buckets with ZERO available conditions are excluded from
    both the score AND the weight normalization -- the score reflects only
    what could actually be judged. `data_completeness` (0-1) is the
    fraction of ALL named conditions that had real data, used to temper
    CONFIDENCE separately from the score itself."""
    bucket_pass: dict[str, list[bool]] = {}
    for name, (bucket, val) in conditions.items():
        bucket_pass.setdefault(bucket, [])
        if val is not None:
            bucket_pass[bucket].append(val)

    available_weight = 0.0
    weighted_sum = 0.0
    bucket_scores = {}
    for bucket, vals in bucket_pass.items():
        w = weights.get(bucket, 0.0)
        if not vals:
            bucket_scores[bucket] = None
            continue
        rate = sum(1 for v in vals if v) / len(vals)
        bucket_scores[bucket] = round(rate * 100.0, 1)
        weighted_sum += w * rate
        available_weight += w

    score = round(100.0 * weighted_sum / available_weight, 1) if available_weight > 0 else 0.0
    n_total = len(conditions)
    n_available = sum(1 for _, v in conditions.values() if v is not None)
    data_completeness = round(n_available / n_total, 3) if n_total else 0.0

    passed = [name for name, (_, v) in conditions.items() if v is True]
    failed = [name for name, (_, v) in conditions.items() if v is False]

    return {"score": score, "bucket_scores": bucket_scores, "data_completeness": data_completeness,
            "conditions_passed": passed, "conditions_failed": failed,
            "available_weight_fraction": round(available_weight, 3)}
