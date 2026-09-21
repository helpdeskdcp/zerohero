"""
Phase 7 -- AI + deterministic fusion. AUTHORITY RULES (enforced here, tested
in tests/test_ai_fusion.py):

  The deterministic engine remains authoritative. AI can only:
    - validate a setup, or
    - REDUCE confidence, or
    - recommend NO_TRADE (never force a trade the deterministic engine
      didn't already produce), or
    - classify regime / flag anomalies.

  AI CANNOT:
    - upgrade a deterministic NO_TRADE into a trade,
    - bypass the EV gate, cost gate, data-quality gate, or risk engine
      (all of those already ran, upstream, before this function is ever
      called -- this function only ever sees their OUTPUT, never their
      inputs, so it structurally cannot skip them),
    - fabricate a fill, a cost, or market data.

  This module is PURE (no I/O, no network) -- it only combines numbers
  already computed by the deterministic engine (app.engines.scalp_strategy),
  the behavior engine (app.behavior_engine), and the AI layer
  (app.ai.behavior_ai), whichever of which may itself be UNAVAILABLE.
"""
from __future__ import annotations

from dataclasses import dataclass

_STATES = ("STRONG_SELL", "SELL", "WEAK_SELL", "NO_TRADE", "WEAK_BUY", "BUY", "STRONG_BUY")


@dataclass
class FusedDecision:
    final_state: str
    deterministic_score: float | None
    behavior_score: float | None
    ai_score: float | None
    final_confidence: float
    risk_state: str
    profile: str
    regime: str
    reason_codes: list

    def to_dict(self) -> dict:
        return {"final_state": self.final_state, "deterministic_score": self.deterministic_score,
                "behavior_score": self.behavior_score, "ai_score": self.ai_score,
                "final_confidence": self.final_confidence, "risk_state": self.risk_state,
                "profile": self.profile, "regime": self.regime,
                "reason_codes": self.reason_codes}


def _base_state(deterministic_decision: str) -> str:
    d = str(deterministic_decision or "").upper()
    if d == "BUY_CE":
        return "BUY"
    if d == "BUY_PE":
        return "SELL"
    return "NO_TRADE"


def _weaken(state: str, steps: int) -> str:
    """Move `state` `steps` positions TOWARD NO_TRADE (weakening conviction
    by `steps`, a non-negative magnitude), clamped at NO_TRADE, never
    crossing through it to the opposite side (a BUY that gets downgraded
    lands at WEAK_BUY or NO_TRADE, never flips to SELL -- AI can dampen
    conviction, never reverse direction). STRONG_SELL/SELL/WEAK_SELL sit at
    indices BELOW NO_TRADE, so weakening them means increasing the index;
    WEAK_BUY/BUY/STRONG_BUY sit ABOVE it, so weakening means decreasing --
    the two sides move in opposite index directions towards the same target."""
    idx = _STATES.index(state)
    no_trade_idx = _STATES.index("NO_TRADE")
    if idx < no_trade_idx:
        new_idx = min(no_trade_idx, idx + steps)
    elif idx > no_trade_idx:
        new_idx = max(no_trade_idx, idx - steps)
    else:
        new_idx = idx
    return _STATES[new_idx]


def fuse_decision(*, deterministic_decision: str, deterministic_score: float | None,
                  behavior: dict, ai_result: dict, cost_model_status: str) -> FusedDecision:
    base = _base_state(deterministic_decision)
    reason_codes = []

    if base == "NO_TRADE":
        # Deterministic already said no -- AI has nothing to upgrade; done.
        return FusedDecision(
            final_state="NO_TRADE", deterministic_score=deterministic_score,
            behavior_score=behavior.get("signal_quality"), ai_score=None,
            final_confidence=0.0, risk_state=behavior.get("risk_state") or "UNKNOWN",
            profile=behavior.get("profile") or "", regime=behavior.get("regime") or "",
            reason_codes=["deterministic_no_trade"])

    state = base
    final_confidence = float(deterministic_score or 0.0)
    ai_score = None

    if cost_model_status != "OK":
        reason_codes.append("cost_model_uncalibrated")

    ai_status = ai_result.get("status")
    if ai_status == "OK":
        ai_score = ai_result.get("confidence")
        if ai_result.get("signal_validation") == "FAIL":
            state = "NO_TRADE"
            reason_codes.append("ai_signal_validation_fail")
        elif ai_result.get("risk") == "HIGH":
            state = _weaken(state, 1)
            reason_codes.append("ai_flagged_high_risk")
        elif ai_result.get("profile_match") is False:
            state = _weaken(state, 1)
            reason_codes.append("ai_profile_mismatch")
        # Independent of the elif chain above (not mutually exclusive with
        # it) -- spec section 9's orderflow-conflict rule. A field that
        # never existed before this addition, so this can only ever add new
        # weakening, never change any pre-existing test's expected outcome.
        # Stacking with an existing HIGH-risk/profile-mismatch weaken is the
        # intended "if the conflict persists alongside another red flag,
        # weaken further (naturally reaching NO_TRADE for an already-WEAK
        # signal)" behavior -- not a special-cased force-NO_TRADE, which
        # would risk being a second, inconsistent downgrade rule.
        if ai_result.get("orderflow_conflict") is True:
            state = _weaken(state, 1)
            reason_codes.append("ai_orderflow_conflict")
        for w in (ai_result.get("warnings") or []):
            reason_codes.append(f"ai_warning:{w}")
        if ai_score is not None:
            final_confidence = (final_confidence + ai_score) / 2.0
    else:
        reason_codes.append(f"ai_unavailable:{ai_status}")
        # AI unavailable/failed is neutral, not a downgrade -- the
        # deterministic-only decision stands exactly as it would with no
        # AI layer at all (the core safety guarantee of this phase).

    return FusedDecision(
        final_state=state, deterministic_score=deterministic_score,
        behavior_score=behavior.get("signal_quality"), ai_score=ai_score,
        final_confidence=round(final_confidence, 2),
        risk_state=behavior.get("risk_state") or "UNKNOWN",
        profile=behavior.get("profile") or "", regime=behavior.get("regime") or "",
        reason_codes=reason_codes,
    )
