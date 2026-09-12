"""
Edge Score + lifecycle state machine -- section 12: "Create explainable:
EDGE SCORE. States: CANDIDATE, VALIDATED, ACTIVE, WEAKENING, DECAYING,
INVALID."

Same design philosophy as app.structural_break.break_score (deliberately
reused, not reinvented): the score is a plain COUNT of independent, named
evidence checks rather than a weighted formula with invented coefficients,
and every state transition requires SUSTAINED evidence across several
evaluations (hysteresis) rather than one reading -- a single lucky/unlucky
window should never validate or invalidate an edge on its own, mirroring
break_score.py's own "never declare structural break from a small losing
streak alone" principle, applied here to edge validation/decay instead of
model health.

Evidence checked (max score 4), each independently named so a report can
say exactly which of the four is/isn't currently supporting the edge:
  1. conditional_edge's 95% CI excludes zero on the positive side (the edge
     is not just "positive by luck of this sample")
  2. Net EV (after realistic costs) is positive
  3. Risk-adjusted EV clears a floor (0.1R -- reuses option_engine.ev_gate's
     own existing min_ev_r default, not a new invented number)
  4. Sample size has reached a size this module treats as "robust" (200 --
     well above conditional_edge.py's own 30-row statistical minimum,
     because validating an EDGE for reliance is a higher bar than merely
     being ABLE to compute a proportion)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

STATES = ("CANDIDATE", "VALIDATED", "ACTIVE", "WEAKENING", "DECAYING", "INVALID")

MAX_SCORE = 4
ROBUST_N = 200
RISK_ADJUSTED_EV_FLOOR = 0.1     # reuses option_engine.ev_gate's own min_ev_r default

VALIDATE_MIN = 3                 # score >= this, sustained, to leave CANDIDATE
VALIDATE_STREAK = 3
ACTIVATE_STREAK = 3              # further sustained evals at/above VALIDATE_MIN, VALIDATED -> ACTIVE
WEAKEN_MAX = 2                   # score <= this (but > 0) sustained -> WEAKENING
WEAKEN_STREAK = 3
DECAY_MAX = 1                    # score <= this sustained further -> DECAYING
DECAY_STREAK = 3
INVALID_STREAK = 5               # score == 0 sustained this long -> INVALID
RECOVER_STREAK = 3                # sustained recovery to move back up one level


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class EdgeEvidence:
    score: int
    max_score: int
    reasons: list

    def to_dict(self) -> dict:
        return asdict(self)


def compute_evidence(conditional_result, ev_result) -> EdgeEvidence:
    """Pure function -- one evaluation's worth of evidence, no history."""
    score = 0
    reasons = []
    if conditional_result.status == "OK" and conditional_result.edge_ci95_low is not None \
            and conditional_result.edge_ci95_low > 0:
        score += 1
        reasons.append("EDGE_CI95_EXCLUDES_ZERO")
    if ev_result.status == "OK" and ev_result.net_ev_points is not None and ev_result.net_ev_points > 0:
        score += 1
        reasons.append("NET_EV_POSITIVE")
    if ev_result.status == "OK" and ev_result.risk_adjusted_ev is not None \
            and ev_result.risk_adjusted_ev >= RISK_ADJUSTED_EV_FLOOR:
        score += 1
        reasons.append(f"RISK_ADJUSTED_EV_ABOVE_{RISK_ADJUSTED_EV_FLOOR}R")
    if conditional_result.n_condition >= ROBUST_N:
        score += 1
        reasons.append(f"SAMPLE_SIZE_ROBUST_{conditional_result.n_condition}")
    return EdgeEvidence(score=score, max_score=MAX_SCORE, reasons=reasons)


@dataclass
class EdgeEvent:
    ts: str
    state_from: str
    state_to: str
    evidence: dict
    reason: str


class EdgeStateMachine:
    """Stateful across calls -- one instance per (instrument, condition_label)
    the caller wants to track. Same scoping convention as
    structural_break.break_score.StructuralBreakStateMachine."""

    def __init__(self):
        self.state = "CANDIDATE"
        self.history: list[dict] = []
        self.events: list[EdgeEvent] = []

    def _recent_scores(self, n: int) -> list[int]:
        return [h["evidence"]["score"] for h in self.history[-n:]]

    def _transition(self, new_state: str, evidence: EdgeEvidence, reason: str):
        if new_state == self.state:
            return
        self.events.append(EdgeEvent(ts=_now_iso(), state_from=self.state, state_to=new_state,
                                     evidence=evidence.to_dict(), reason=reason))
        self.state = new_state

    def evaluate(self, conditional_result, ev_result) -> dict:
        evidence = compute_evidence(conditional_result, ev_result)
        self.history.append({"ts": _now_iso(), "evidence": evidence.to_dict(), "state": self.state})

        if conditional_result.status != "OK" or ev_result.status not in ("OK", "UNCALIBRATED_COST"):
            # not enough data (or cost model gap) to say anything either way --
            # stay put rather than force a verdict on absent evidence
            pass

        elif self.state == "CANDIDATE":
            recent = self._recent_scores(VALIDATE_STREAK)
            if len(recent) >= VALIDATE_STREAK and all(s >= VALIDATE_MIN for s in recent):
                self._transition("VALIDATED", evidence,
                                 f"score >= {VALIDATE_MIN} for {VALIDATE_STREAK} consecutive evals")

        elif self.state == "VALIDATED":
            recent = self._recent_scores(ACTIVATE_STREAK)
            if len(recent) >= ACTIVATE_STREAK and all(s >= VALIDATE_MIN for s in recent):
                self._transition("ACTIVE", evidence,
                                 f"sustained >= {VALIDATE_MIN} for a further {ACTIVATE_STREAK} evals")
            elif evidence.score < VALIDATE_MIN:
                # didn't hold up even before reaching ACTIVE -- back to CANDIDATE,
                # not straight to WEAKENING/DECAYING (it was never trusted yet)
                self._transition("CANDIDATE", evidence, "evidence dropped before reaching ACTIVE")

        elif self.state == "ACTIVE":
            recent = self._recent_scores(WEAKEN_STREAK)
            if len(recent) >= WEAKEN_STREAK and all(0 < s <= WEAKEN_MAX for s in recent):
                self._transition("WEAKENING", evidence,
                                 f"score <= {WEAKEN_MAX} for {WEAKEN_STREAK} consecutive evals")
            elif len(recent) >= WEAKEN_STREAK and all(s == 0 for s in recent):
                # a sharp, sustained collapse can skip straight past WEAKENING
                self._transition("DECAYING", evidence,
                                 f"score collapsed to 0 for {WEAKEN_STREAK} consecutive evals")

        elif self.state == "WEAKENING":
            recent_recover = self._recent_scores(RECOVER_STREAK)
            recent_decay = self._recent_scores(DECAY_STREAK)
            if len(recent_recover) >= RECOVER_STREAK and all(s >= VALIDATE_MIN for s in recent_recover):
                self._transition("ACTIVE", evidence,
                                 f"recovered to >= {VALIDATE_MIN} for {RECOVER_STREAK} evals")
            elif len(recent_decay) >= DECAY_STREAK and all(s <= DECAY_MAX for s in recent_decay):
                self._transition("DECAYING", evidence,
                                 f"score <= {DECAY_MAX} for {DECAY_STREAK} consecutive evals")

        elif self.state == "DECAYING":
            recent_recover = self._recent_scores(RECOVER_STREAK)
            recent_invalid = self._recent_scores(INVALID_STREAK)
            if len(recent_recover) >= RECOVER_STREAK and all(s >= VALIDATE_MIN for s in recent_recover):
                self._transition("ACTIVE", evidence,
                                 f"recovered to >= {VALIDATE_MIN} for {RECOVER_STREAK} evals")
            elif len(recent_invalid) >= INVALID_STREAK and all(s == 0 for s in recent_invalid):
                self._transition("INVALID", evidence,
                                 f"score == 0 for {INVALID_STREAK} consecutive evals")

        elif self.state == "INVALID":
            pass   # terminal for this instance -- a genuinely new edge needs a new tracker

        return {"state": self.state, "evidence": evidence.to_dict(), "n_evaluations": len(self.history)}
