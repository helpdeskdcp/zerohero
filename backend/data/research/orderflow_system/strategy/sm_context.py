"""
Stage 0 -- Smart Money Context Engine (spec Section 3). Combines the SM-x
detectors into a per-bar context label. This is CONTEXT ONLY -- it never
generates an entry; setups 1-4 read it as an optional pre-condition gate
(strategy/_common.py's SetupBase.sm_gate_ok(), spec Section 4).

side       : "long" | "short" | "neutral" -- the weighted-majority direction
             among detectors that fired, using cfg["smartmoney"]["sm_weights"]
phase      : from the Wyckoff detector alone (the one detector that
             specifically models phase) -- "accumulation"/"distribution"/
             "markup"/"markdown"/"unknown"
events     : every detector that fired this bar, with its own detail dict
bias_score : 0..1, weighted sum of (weight * strength) for detectors whose
             direction agrees with `side` (if bias_direction_agree_only),
             normalized by the total weight of detectors that fired at all
is_proxy   : True if ANY contributing detector is a Mode-C approximation
             (which, for every detector implemented so far except fvg/
             liquidity_sweep/structure_shift, is always true right now --
             see each detector module's own docstring)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .smartmoney import (absorption, divergence, fvg, liquidity_sweep,
                         order_flow_imbalance, structure_shift, value_migration, wyckoff)

_DETECTORS = {
    "fvg": fvg, "liquidity_sweep": liquidity_sweep, "structure_shift": structure_shift,
    "wyckoff": wyckoff, "ofi": order_flow_imbalance, "absorption": absorption,
    "divergence": divergence, "value_migration": value_migration,
}


@dataclass(frozen=True)
class SMContext:
    side: str
    phase: str
    events: list = field(default_factory=list)
    bias_score: float = 0.0
    is_proxy: bool = True


def compute_context(df, i: int, cfg: dict) -> SMContext:
    smcfg = cfg["smartmoney"]
    weights = smcfg["sm_weights"]
    results = {name: mod.detect(df, i, smcfg) for name, mod in _DETECTORS.items()}

    fired = {name: r for name, r in results.items() if r.present}
    events = [{"detector": name, "direction": r.direction, "strength": round(r.strength, 4),
              "confidence": round(r.confidence, 4), "is_proxy": r.is_proxy, "detail": r.detail}
             for name, r in fired.items()]
    is_proxy = any(r.is_proxy for r in fired.values()) if fired else True

    bull_weight = sum(weights.get(n, 0) * r.strength for n, r in fired.items() if r.direction == "bull")
    bear_weight = sum(weights.get(n, 0) * r.strength for n, r in fired.items() if r.direction == "bear")
    if bull_weight == 0 and bear_weight == 0:
        side = "neutral"
    else:
        side = "long" if bull_weight > bear_weight else "short"

    total_fired_weight = sum(weights.get(n, 0) for n in fired) or 1.0
    if smcfg["bias_direction_agree_only"]:
        agree_direction = "bull" if side == "long" else ("bear" if side == "short" else None)
        agree_weight = sum(weights.get(n, 0) * r.strength for n, r in fired.items()
                          if r.direction == agree_direction) if agree_direction else 0.0
        bias_score = min(agree_weight / total_fired_weight, 1.0) if total_fired_weight else 0.0
    else:
        bias_score = min((bull_weight + bear_weight) / total_fired_weight, 1.0) if total_fired_weight else 0.0

    wyckoff_result = results["wyckoff"]
    phase = wyckoff_result.detail["phase"] if wyckoff_result.present and wyckoff_result.detail else "unknown"

    return SMContext(side=side, phase=phase, events=events, bias_score=round(bias_score, 4), is_proxy=is_proxy)


def sm_gate_ok(ctx: SMContext, direction: str, cfg: dict) -> bool:
    """direction: "bull" or "bear" (the setup's own direction). Checks the
    context against setup1-4's require_sm_alignment/sm_min_bias/
    sm_required_events config -- spec Section 4's gate."""
    want_side = "long" if direction == "bull" else "short"
    if ctx.side != want_side:
        return False
    if ctx.bias_score < cfg["sm_min_bias"]:
        return False
    required = cfg.get("sm_required_events") or []
    if required:
        fired_events = {e["detector"] for e in ctx.events}
        if not set(required).issubset(fired_events):
            return False
    return True
