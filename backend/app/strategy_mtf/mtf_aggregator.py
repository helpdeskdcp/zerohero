"""
Weighted multi-timeframe aggregation + the approved CE/PE/NO_TRADE decision
rule. Monthly bias acts as a ceiling: LTF evidence that contradicts it is
only allowed through at a materially higher ("overwhelming evidence") bar,
never silently overridden by ordinary LTF noise.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ..strategy.config import StrategyConfig
from .mtf_config import TIMEFRAME_LOOKBACK_5M_BARS, MTFConfig
from .timeframe_bias import BEARISH, BULLISH, NEUTRAL, TimeframeBias, compute_bias

TIMEFRAME_ORDER = ("1mo", "1w", "1d", "1h", "30m", "15m", "5m")


@dataclass
class AggregateResult:
    direction: str                     # "CE" | "PE" | "NO_TRADE"
    status: str
    aggregate_score: float
    bull_score: float
    bear_score: float
    monthly_bias: str
    per_timeframe: dict                # tf -> TimeframeBias.to_dict()
    reason: str
    data_available_ts: str | None

    def to_dict(self) -> dict:
        return {**asdict(self), "per_timeframe": self.per_timeframe}


def compute_aggregate(bars_5m: list[dict], *, as_of_ts: str, cfg: MTFConfig | None = None,
                      strategy_cfg: StrategyConfig | None = None) -> AggregateResult:
    """`bars_5m`: real 5m bars, oldest-first, already causal (every bar's
    own timestamp <= as_of_ts). Should cover at least
    `mtf_config.TIMEFRAME_LOOKBACK_5M_BARS["1mo"]` bars for a real Monthly
    read; timeframes with too little history in `bars_5m` degrade to
    NEUTRAL/0 gracefully (see timeframe_bias.compute_bias), never crash."""
    cfg = cfg or MTFConfig()
    strategy_cfg = strategy_cfg or StrategyConfig(sma_fast=cfg.sma_fast, sma_slow=cfg.sma_slow,
                                                  slope_lookback=cfg.slope_lookback)
    weights = cfg.normalized_weights()

    biases: dict[str, TimeframeBias] = {}
    for tf in TIMEFRAME_ORDER:
        lookback = TIMEFRAME_LOOKBACK_5M_BARS.get(tf, len(bars_5m))
        window = bars_5m[-lookback:] if lookback < len(bars_5m) else bars_5m
        biases[tf] = compute_bias(window, tf, as_of_ts=as_of_ts, cfg=strategy_cfg)

    # A timeframe with genuinely insufficient CONFIRMED history (sma_slow is
    # None) has its weight excluded from BOTH the numerator and the
    # denominator -- missing data degrades the score honestly (renormalized
    # among what's actually available), it never silently caps the maximum
    # achievable score just because one timeframe (e.g. Monthly, which needs
    # ~20+ real confirmed months) doesn't have enough real history yet. A
    # timeframe that DID compute a real reading and came out NEUTRAL still
    # counts fully -- that's genuine indecision, not missing data.
    available_weight = sum(weights[tf] for tf, b in biases.items() if b.sma_slow is not None) or 1.0
    bull_score = sum(weights[tf] * (b.score if b.direction == BULLISH else 0.0)
                    for tf, b in biases.items() if b.sma_slow is not None) / available_weight
    bear_score = sum(weights[tf] * (b.score if b.direction == BEARISH else 0.0)
                    for tf, b in biases.items() if b.sma_slow is not None) / available_weight
    aggregate_direction = BULLISH if bull_score > bear_score else (BEARISH if bear_score > bull_score else NEUTRAL)
    aggregate_score = round(max(bull_score, bear_score), 1)
    monthly_bias = biases["1mo"].direction

    per_tf_dict = {tf: b.to_dict() for tf, b in biases.items()}

    if aggregate_score < cfg.minimum_threshold:
        return AggregateResult(direction="NO_TRADE", status="BELOW_THRESHOLD",
                               aggregate_score=aggregate_score, bull_score=round(bull_score, 1),
                               bear_score=round(bear_score, 1), monthly_bias=monthly_bias,
                               per_timeframe=per_tf_dict,
                               reason=f"aggregate {aggregate_score} < threshold {cfg.minimum_threshold}",
                               data_available_ts=as_of_ts)

    agrees_with_monthly = monthly_bias == NEUTRAL or aggregate_direction == monthly_bias
    if agrees_with_monthly:
        direction = "CE" if aggregate_direction == BULLISH else "PE"
        return AggregateResult(direction=direction, status="AGGREGATE_CONFIRMED",
                               aggregate_score=aggregate_score, bull_score=round(bull_score, 1),
                               bear_score=round(bear_score, 1), monthly_bias=monthly_bias,
                               per_timeframe=per_tf_dict,
                               reason=f"aggregate {aggregate_score} >= {cfg.minimum_threshold} and agrees with "
                                     f"monthly bias {monthly_bias}",
                               data_available_ts=as_of_ts)

    if aggregate_score >= cfg.monthly_override_threshold:
        direction = "CE" if aggregate_direction == BULLISH else "PE"
        return AggregateResult(direction=direction, status="MONTHLY_OVERRIDE",
                               aggregate_score=aggregate_score, bull_score=round(bull_score, 1),
                               bear_score=round(bear_score, 1), monthly_bias=monthly_bias,
                               per_timeframe=per_tf_dict,
                               reason=f"aggregate {aggregate_score} >= override bar "
                                     f"{cfg.monthly_override_threshold} despite monthly bias {monthly_bias}",
                               data_available_ts=as_of_ts)

    return AggregateResult(direction="NO_TRADE", status="MONTHLY_BIAS_CONFLICT",
                           aggregate_score=aggregate_score, bull_score=round(bull_score, 1),
                           bear_score=round(bear_score, 1), monthly_bias=monthly_bias,
                           per_timeframe=per_tf_dict,
                           reason=f"aggregate direction {aggregate_direction} contradicts monthly bias "
                                 f"{monthly_bias} and does not clear the override bar "
                                 f"{cfg.monthly_override_threshold} (got {aggregate_score})",
                           data_available_ts=as_of_ts)
