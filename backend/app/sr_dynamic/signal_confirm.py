"""
Live-wiring spec sections 8-10: SR as a CONFIRMATION/CONTEXT layer for
BUY_CE/BUY_PE (never an unconditional trigger), a transparent SR_SCORE, and
a room-to-move filter using the existing expected-index-move figure
(`index_move_pts`, already computed in app.engines.scalp_strategy.decide_from_context
for option_engine._translation -- reused here, not recomputed).

Mirrors the existing option-chain gate's contract exactly
(app.engines.scalp_strategy._chain_bias): verdict in
{CONFIRM, CONTRADICT, NEUTRAL, INSUFFICIENT}. INSUFFICIENT never manufactures
a confidence delta. This function does not decide anything by itself -- the
caller (scalp_strategy.py's opt-in SR gate) decides what CONTRADICT does
(veto vs. confidence step), exactly like the chain gate already does.
"""
from __future__ import annotations

from .live_state import LiveSRState

INSUFFICIENT, CONFIRM, CONTRADICT, NEUTRAL = "INSUFFICIENT", "CONFIRM", "CONTRADICT", "NEUTRAL"


def evaluate_sr_confirmation(want: str, live_sr: LiveSRState | None, *,
                             index_move_pts: float | None, min_room_ratio: float = 1.0) -> dict:
    """`want`: 'CE' or 'PE'. Returns a dict with verdict/reason/sr_score/
    sr_confidence/positives/negatives/components -- every component is
    named so the caller (and the audit log) can explain the verdict."""
    if live_sr is None or live_sr.state in ("NO_ZONES", "INSUFFICIENT_ZONES", "NO_DATA"):
        return {"verdict": INSUFFICIENT, "reason": "no live SR state", "sr_score": None,
                "sr_confidence": None, "positives": [], "negatives": [], "components": {}}

    pos: list[str] = []
    neg: list[str] = []

    if want == "CE":
        if live_sr.support_rejection:
            pos.append("support_rejection")
        if live_sr.breakout_confirmed:
            pos.append("resistance_breakout_confirmed")
        elif live_sr.resistance_breakout and live_sr.resistance_retest_status == "PENDING":
            pos.append("resistance_breakout_pending_retest")

        if live_sr.resistance_touch and not live_sr.resistance_breakout:
            neg.append("at_unbroken_resistance")
        if live_sr.resistance_rejection:
            neg.append("resistance_rejection")
        if live_sr.resistance_retest_status == "FAILED":
            neg.append("failed_resistance_breakout")

        room_distance = live_sr.resistance_distance
    else:
        if live_sr.resistance_rejection:
            pos.append("resistance_rejection")
        if live_sr.resistance_retest_status == "FAILED":
            pos.append("resistance_breakout_failure")
        if live_sr.breakdown_confirmed:
            pos.append("support_breakdown_confirmed")
        elif live_sr.support_breakdown and live_sr.support_retest_status == "PENDING":
            pos.append("support_breakdown_pending_retest")

        if live_sr.support_touch and not live_sr.support_breakdown:
            neg.append("strong_support_below")
        if live_sr.support_rejection:
            neg.append("support_rejection")
        if live_sr.support_retest_status == "FAILED":
            neg.append("failed_support_breakdown")

        room_distance = live_sr.support_distance

    room_ratio = None
    if room_distance is not None and index_move_pts:
        room_ratio = round(abs(room_distance) / index_move_pts, 3)
        if room_ratio < min_room_ratio:
            neg.append(f"insufficient_room(ratio={room_ratio})")

    zone_strength = live_sr.zone_strength or 0.0
    reaction_score = live_sr.sr_reaction_score or 0.0
    room_bonus = 10.0 if (room_ratio is not None and room_ratio >= min_room_ratio) else 0.0
    sr_score = round(max(0.0, min(100.0,
        0.4 * zone_strength + 0.3 * reaction_score + 20.0 * len(pos) - 15.0 * len(neg) + room_bonus)), 1)

    if pos and not neg:
        verdict = CONFIRM
    elif neg and not pos:
        verdict = CONTRADICT
    elif pos and neg:
        verdict = CONFIRM if len(pos) > len(neg) else CONTRADICT
    else:
        verdict = NEUTRAL

    reason = ", ".join(pos + [f"NOT:{n}" for n in neg]) or "no active SR condition near price"
    return {"verdict": verdict, "reason": reason, "sr_score": sr_score,
            "sr_confidence": live_sr.sr_confidence, "positives": pos, "negatives": neg,
            "components": {"zone_strength": zone_strength, "reaction_score": reaction_score,
                           "room_ratio": room_ratio, "n_positive": len(pos), "n_negative": len(neg)}}
