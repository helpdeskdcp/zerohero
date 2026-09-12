"""
Adaptation mode -- section E of the spec.

Provides the MECHANISM for "reduce confidence / move to NO-TRADE on a
confirmed structural break," hooked into the EXISTING risk gate
(app.autoscalp.safeguards.Safeguards) rather than a parallel one -- per
the investigation, Safeguards.check_entry() already has a sticky
`_halt_reason` used for the daily-loss-cap halt; `set_external_halt()`
(added alongside this file) is the same mechanism, exposed as a small
public method instead of reaching into a private field from here.

Deliberately NOT auto-wired into app/autoscalp/runner.py's live decision
loop by this file -- that's a separate, explicit step (this module gives the
function; actually calling it every cycle from the live runner is a
decision that touches the live paper-trading loop and deserves its own
review, not a side-effect of building the structural-break layer). AutoScalp
is PAPER-only regardless, but "provide the mechanism, wire it in
deliberately later" matches this whole session's practice for anything that
touches a live decision path.

Section E's steps 4-8 (collect new-regime data, re-specify, re-parameterize,
walk-forward validate, keep candidate in SHADOW) belong to shadow.py and
backtest_compare.py -- not built yet. This file owns steps 1-3 (freeze
confidence in the old model, mark it degrading/broken, gate via Safeguards)
plus the adaptation-lag bookkeeping from section H.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

# States where the OLD model must not be trusted for new entries.
HALT_STATES = ("STRUCTURAL_BREAK", "ADAPTATION")
# States where a confirmed halt should be lifted (RECOVERED = candidate
# promoted and proven; NORMAL/WATCH/DEGRADING never had a halt to begin
# with, but clearing is idempotent and harmless if called anyway).
CLEAR_STATES = ("NORMAL", "WATCH", "DEGRADING", "RECOVERED")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class AdaptationStatus:
    """Section H's explicit tracking: adaptation state, observations
    collected, validation progress, candidate performance kept SEPARATE from
    old-model performance (the candidate's numbers live in shadow.py's own
    output once that exists -- this dataclass just carries the pointer/count,
    never mixes the two)."""
    state: str
    is_halted: bool
    old_model_id: str | None = None
    adaptation_observations: int = 0
    candidate_model_id: str | None = None
    validation_status: str = "NOT_STARTED"   # NOT_STARTED | IN_PROGRESS | PASSED | FAILED
    entered_state_ts: str | None = None

    def to_dict(self):
        return asdict(self)


def apply_to_safeguards(safeguards, state: str, *, reason: str = "", old_model_id: str | None = None) -> bool:
    """Call once per structural-break evaluation with the state machine's
    current `state`. Returns True if a halt is (now) active.

    Deliberately conservative in one direction only: this function can HALT
    trading (tightening an existing risk control) or leave an unrelated halt
    untouched, but it will only CLEAR a halt it can identify as its own (the
    reason string is prefixed so a daily-loss-cap halt set by Safeguards
    itself is never accidentally cleared by this layer)."""
    prefix = "STRUCTURAL_BREAK:"
    current = getattr(safeguards, "_halt_reason", None)

    if state in HALT_STATES:
        msg = f"{prefix} {state}" + (f" ({reason})" if reason else "") + \
              (f" [old_model={old_model_id}]" if old_model_id else "")
        safeguards.set_external_halt(msg)
        return True

    if state in CLEAR_STATES and current and str(current).startswith(prefix):
        safeguards.set_external_halt(None)
        return False

    return bool(current)


class AdaptationTracker:
    """Stateful bookkeeping companion to break_score.StructuralBreakStateMachine
    -- tracks section H's fields across the ADAPTATION/VALIDATION lifecycle.
    One instance per (symbol[, regime]), same scoping convention as the state
    machine it sits beside."""

    def __init__(self):
        self.candidate_model_id: str | None = None
        self.validation_status = "NOT_STARTED"
        self._last_state: str | None = None
        self._entered_state_ts: str | None = None

    def update(self, state: str, *, adaptation_observations: int = 0,
               old_model_id: str | None = None) -> AdaptationStatus:
        if state != self._last_state:
            self._entered_state_ts = _now_iso()
            self._last_state = state
            if state == "ADAPTATION":
                # a fresh adaptation episode starts with no candidate yet --
                # shadow.py is responsible for creating/assigning one
                self.candidate_model_id = None
                self.validation_status = "NOT_STARTED"
            elif state == "VALIDATION":
                self.validation_status = "IN_PROGRESS"
            elif state == "RECOVERED":
                self.validation_status = "PASSED"

        return AdaptationStatus(
            state=state, is_halted=state in HALT_STATES, old_model_id=old_model_id,
            adaptation_observations=adaptation_observations,
            candidate_model_id=self.candidate_model_id,
            validation_status=self.validation_status,
            entered_state_ts=self._entered_state_ts,
        )

    def assign_candidate(self, candidate_model_id: str) -> None:
        self.candidate_model_id = candidate_model_id
