"""Bearish (PE) weighted strategy -- mirror of ce_strategy.py."""
from __future__ import annotations

from .base_strategy import MarketFeatures, StrategyResult, build_indicator_snapshot
from .scoring import bearish_conditions, weighted_score

NAME = "PE_TREND_MOMENTUM_V1"


def evaluate_pe(features: MarketFeatures, cfg, ind=None) -> StrategyResult:
    ind = ind if ind is not None else build_indicator_snapshot(features, cfg)
    if ind is None:
        return StrategyResult(strategy_name=NAME, direction="NEUTRAL", valid=False, score=0.0,
                              confidence=0.0, reason="insufficient bar history", entry_allowed=False)

    conditions = bearish_conditions(ind, features, cfg)
    result = weighted_score(conditions, cfg.normalized_weights("PE"))
    score = result["score"]
    confidence = score * result["data_completeness"]

    # --- explicit 5 SMA hard rule (spec: "do NOT generate a strong PE
    # signal while price is clearly above 5 SMA") ---
    wrong_side = ind.sma_fast is not None and ind.close > ind.sma_fast
    if wrong_side:
        confidence *= cfg.wrong_side_penalty
        if "price_below_5sma" not in result["conditions_failed"]:
            result["conditions_failed"].append("price_below_5sma")

    valid = score >= cfg.minimum_threshold and not wrong_side
    reasons = []
    if result["conditions_passed"]:
        reasons.append(f"{len(result['conditions_passed'])} bearish conditions confirmed")
    if wrong_side:
        reasons.append("price is above 5 SMA -- confidence penalised")
    if result["conditions_failed"] and not wrong_side:
        reasons.append(f"{len(result['conditions_failed'])} conditions failed")

    return StrategyResult(
        strategy_name=NAME, direction="PE" if score > 0 else "NEUTRAL",
        valid=bool(valid), score=round(score, 1), confidence=round(max(0.0, min(100.0, confidence)), 1),
        conditions_passed=result["conditions_passed"], conditions_failed=result["conditions_failed"],
        reason="; ".join(reasons) or "no bearish evidence", entry_allowed=bool(valid),
    )
