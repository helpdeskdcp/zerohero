"""
Two separate decisions, per the spec's own two worked-example sections:

1. CE vs PE, from the two strategy scores alone (`decide_direction`).
2. Does that decision agree with the EXISTING raw signal (`resolve`)? A
   disagreement is a SIGNAL_CONFLICT and resolves to NO_TRADE unless
   `cfg.allow_conflict_override` is explicitly set -- never silently traded.
"""
from __future__ import annotations

from .base_strategy import StrategyResult

RAW_TO_SIDE = {"BUY": "CE", "BULLISH": "CE", "SELL": "PE", "BEARISH": "PE"}


def decide_direction(ce: StrategyResult, pe: StrategyResult, cfg) -> dict:
    """The spec's own decision table, verbatim:
        CE if ce.score >= threshold AND ce.score - pe.score >= margin
        PE if pe.score >= threshold AND pe.score - ce.score >= margin
        else NO_TRADE (never forced when both sides are weak or close)."""
    if ce.score >= cfg.minimum_threshold and (ce.score - pe.score) >= cfg.minimum_margin:
        return {"direction": "CE", "status": "STRATEGY_CE", "reason": f"CE {ce.score} >= "
               f"{cfg.minimum_threshold} and beats PE {pe.score} by >= {cfg.minimum_margin}"}
    if pe.score >= cfg.minimum_threshold and (pe.score - ce.score) >= cfg.minimum_margin:
        return {"direction": "PE", "status": "STRATEGY_PE", "reason": f"PE {pe.score} >= "
               f"{cfg.minimum_threshold} and beats CE {ce.score} by >= {cfg.minimum_margin}"}
    return {"direction": "NO_TRADE", "status": "NO_TRADE",
           "reason": f"neither side cleared threshold={cfg.minimum_threshold} with margin="
                     f"{cfg.minimum_margin} (CE={ce.score}, PE={pe.score})"}


def resolve(raw_signal: str | None, ce: StrategyResult, pe: StrategyResult, cfg) -> dict:
    decision = decide_direction(ce, pe, cfg)
    raw_side = RAW_TO_SIDE.get(str(raw_signal or "").upper())

    if raw_side is None or decision["direction"] == "NO_TRADE":
        return decision   # nothing to conflict-check against, or already NO_TRADE

    if raw_side != decision["direction"]:
        conflict = {"direction": "NO_TRADE", "status": "SIGNAL_CONFLICT",
                   "raw_signal": raw_signal, "strategy_direction": decision["direction"],
                   "reason": f"raw signal implies {raw_side} but strategy scored "
                             f"{decision['direction']} (CE={ce.score}, PE={pe.score})"}
        if cfg.allow_conflict_override:
            conflict["direction"] = decision["direction"]
            conflict["status"] = "SIGNAL_CONFLICT_OVERRIDDEN"
        return conflict

    return decision
