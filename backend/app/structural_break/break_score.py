"""
Structural break score + state machine -- section D of the spec.

Design choice, stated up front: the score is a plain COUNT of independent,
named pieces of evidence currently triggered, not a weighted-sum formula
with invented coefficients. "7 of 16 independent checks are currently
flagging a problem, specifically: X, Y, Z" is a genuinely explainable
statement; "score = 0.3*a + 0.4*b + 0.3*c" is not any more explainable just
because someone picked 0.3/0.4/0.3 -- those weights would themselves need
the same "why this number" justification drift.py's thresholds needed, and
nobody has validated them yet (that's what section J's backtest is for).
Counting evidence sidesteps inventing weights before there's data to justify
them, while still producing one number and a legible category breakdown.

The spec's 5 additive terms in section D map onto the 3 monitor modules
already built as follows (documented once here rather than pretending
these are 5 independent computations):
  - performance deterioration      -> performance_monitor.py output
  - prediction deterioration       -> prediction_drift.py output
  - feature distribution drift     -> feature_drift.py output (all features
                                        except regime/atr, counted here)
  - volatility / regime change     -> feature_drift.py's "regime" (PSI) and
                                        "atr" (KS) results specifically,
                                        pulled into their own category
                                        because the spec names them
                                        separately, not because they are a
                                        different computation
  - statistical drift evidence     -> not a 4th independent number; this
                                        IS what CUSUM/Page-Hinkley/KS/PSI
                                        collectively are, already counted
                                        above. Reported here as a summary
                                        total across categories, not
                                        double-added into the score.

States and the escalation rule (hysteresis) directly implement "do NOT
declare a structural break from a small losing streak alone":
  NORMAL -> WATCH          : >=1 evaluation with score >= WATCH_MIN
  WATCH -> DEGRADING       : score >= DEGRADING_MIN sustained for
                             DEGRADING_STREAK consecutive evaluations
  DEGRADING -> STRUCTURAL_BREAK : score >= BREAK_MIN sustained for
                             BREAK_STREAK consecutive evaluations, AND
                             spanning at least BREAK_MIN_CATEGORIES of the
                             4 evidence categories (never just one noisy
                             category repeating) -- this specific
                             "multiple independent categories, not one
                             repeated metric" requirement is what makes a
                             losing streak alone (which only ever moves the
                             performance category) structurally incapable of
                             reaching STRUCTURAL_BREAK on its own.
  STRUCTURAL_BREAK -> ADAPTATION : automatic (spec E: stop trusting the old
                             model, start collecting new-regime data)
  De-escalation mirrors escalation: a documented number of consecutive
  LOW-evidence evaluations steps the state back down one level at a time,
  never straight from STRUCTURAL_BREAK to NORMAL in one step.

ADAPTATION -> VALIDATION -> RECOVERED, now that shadow.py exists:
  ADAPTATION -> VALIDATION : automatic once ADAPTATION_MIN_OBSERVATIONS
                             evaluations have been collected (gives the
                             candidate a reasonable minimum runway before
                             judging it at all -- not zero).
  VALIDATION -> RECOVERED  : shadow.ShadowValidator requires
                             CONSECUTIVE_PASSES_REQUIRED consecutive
                             passing comparisons (old-vs-candidate,
                             expectancy must not regress + a majority of
                             the other core metrics better-or-equal) --
                             same hysteresis principle as escalation: one
                             good comparison could be noise.
  VALIDATION -> ADAPTATION : if the candidate hasn't cleared validation
                             within VALIDATION_MAX_ATTEMPTS attempts, revert
                             to ADAPTATION (fresh observation count + a new
                             ShadowValidator) rather than sitting in
                             VALIDATION forever waiting on a candidate that
                             may need re-tuning outside this layer.
  Both transitions require the caller to pass a `candidate_window` (a
  performance_monitor.WindowMetrics.to_dict()-shaped report for whatever
  candidate model is being shadowed) into `evaluate()`; when omitted, this
  machine simply keeps collecting observations and does not force a
  decision on absent data.

RECOVERED is TERMINAL within a single StructuralBreakStateMachine instance
-- it does not loop back to NORMAL automatically. Once a candidate is
promoted, "normal" now means something different (a new model), so a fresh
baseline belongs to a NEW instance (this one's history stays in audit_log.py
for the record) rather than reusing this instance's drift baselines, which
were fit to the OLD model. This is a deliberate boundary, not an oversight.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .shadow import ShadowValidator

STATES = ("NORMAL", "WATCH", "DEGRADING", "STRUCTURAL_BREAK", "ADAPTATION",
          "VALIDATION", "RECOVERED")

# How many ADAPTATION-state evaluations to collect before even attempting to
# validate a candidate -- reuses DEGRADING_STREAK-scale reasoning (a handful
# of evaluations, not one) rather than inventing an unrelated number.
ADAPTATION_MIN_OBSERVATIONS = 5
# How many VALIDATION-state attempts to allow before giving up on this
# candidate and reverting to ADAPTATION for further collection/re-tuning.
VALIDATION_MAX_ATTEMPTS = 10

# Escalation thresholds. Each is a deliberate, documented choice (not
# validated against real outcome data yet -- that validation is section J's
# job; these are the STARTING hypothesis, explicitly not claimed as final):
WATCH_MIN = 2                 # >=2 of 16 checks -- more than a single noisy metric
DEGRADING_MIN = 4
DEGRADING_STREAK = 3          # sustained over 3 consecutive evaluations, not one
BREAK_MIN = 6
BREAK_STREAK = 5
BREAK_MIN_CATEGORIES = 2      # must span >=2 of the 4 categories, not one repeated metric
DEESCALATE_STREAK = 5         # 5 consecutive low-evidence evals to step back down one level

CATEGORY_MAX = {"performance": 4, "prediction": 3, "feature_drift": 7, "regime_volatility": 2}
TOTAL_MAX = sum(CATEGORY_MAX.values())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class BreakEvidence:
    total_score: int
    max_possible: int
    category_scores: dict
    reasons: list

    def to_dict(self):
        return asdict(self)


def compute_evidence(perf_report: dict, feature_report: dict, prediction_report: dict) -> BreakEvidence:
    """Pure function -- no history, no state. Takes ONE call's worth of the
    three monitors' output and returns the evidence for that moment alone."""
    reasons = []
    perf_score = 0
    short, medium, long_ = perf_report.get("short", {}), perf_report.get("medium", {}), perf_report.get("long", {})
    if short.get("status") == "OK" and long_.get("status") == "OK":
        sw, lw = short.get("win_rate"), long_.get("win_rate")
        if sw is not None and lw is not None and sw < lw - 0.15:
            perf_score += 1
            reasons.append(f"SHORT_WIN_RATE_DOWN_{round((lw - sw) * 100)}PP_VS_LONG")
        se, le = short.get("expectancy_points"), long_.get("expectancy_points")
        if se is not None and le is not None and le > 0 and se <= 0:
            perf_score += 1
            reasons.append("EXPECTANCY_TURNED_NEGATIVE_VS_HEALTHY_LONG_BASELINE")
        spf, lpf = short.get("profit_factor"), long_.get("profit_factor")
        if spf is not None and lpf is not None and spf < 1.0 and lpf >= 1.2:
            perf_score += 1
            reasons.append("PROFIT_FACTOR_BELOW_1_VS_HEALTHY_LONG_BASELINE")
        if (short.get("consecutive_losses_current") or 0) >= 5:
            perf_score += 1
            reasons.append(f"CONSECUTIVE_LOSSES_{short['consecutive_losses_current']}"
                            "_CONTRIBUTORY_ONLY_NOT_SUFFICIENT_ALONE")

    pred_score = 0
    for name, r in (prediction_report.get("streams") or {}).items():
        if r.get("triggered"):
            pred_score += 1
            reasons.append(f"PREDICTION_DRIFT_{name.upper()}")

    feature_score = 0
    regime_vol_score = 0
    for name, r in (feature_report.get("features") or {}).items():
        if not r.get("triggered"):
            continue
        if name in ("regime", "atr"):
            regime_vol_score += 1
            reasons.append(f"REGIME_OR_VOLATILITY_SHIFT_{name.upper()}")
        else:
            feature_score += 1
            reasons.append(f"FEATURE_DRIFT_{name.upper()}")

    category_scores = {"performance": perf_score, "prediction": pred_score,
                        "feature_drift": feature_score, "regime_volatility": regime_vol_score}
    total = sum(category_scores.values())
    return BreakEvidence(total_score=total, max_possible=TOTAL_MAX,
                          category_scores=category_scores, reasons=reasons)


@dataclass
class BreakEvent:
    ts: str
    state_from: str
    state_to: str
    evidence: dict
    reason: str


class StructuralBreakStateMachine:
    """Stateful ACROSS calls -- this is the one piece in this layer that must
    remember history, because hysteresis (the whole point of section D's
    "sustained evidence" requirement) is impossible without it. One instance
    per (symbol, regime) or per (symbol) as the caller chooses to scope it --
    this class doesn't know or care what it's tracking break state FOR,
    it only tracks the score sequence it's fed.
    """

    def __init__(self):
        self.state = "NORMAL"
        self.history: list[dict] = []          # [{ts, evidence, state}]
        self.events: list[BreakEvent] = []      # every state TRANSITION, for audit_log.py
        self.adaptation_observations = 0
        self.shadow_validator: ShadowValidator | None = None
        self._last_shadow_result: dict | None = None

    def _recent_scores(self, n: int) -> list[int]:
        return [h["evidence"]["total_score"] for h in self.history[-n:]]

    def _recent_categories_touched(self, n: int) -> set:
        touched = set()
        for h in self.history[-n:]:
            for cat, v in h["evidence"]["category_scores"].items():
                if v > 0:
                    touched.add(cat)
        return touched

    def _transition(self, new_state: str, evidence: BreakEvidence, reason: str):
        if new_state == self.state:
            return
        self.events.append(BreakEvent(ts=_now_iso(), state_from=self.state, state_to=new_state,
                                       evidence=evidence.to_dict(), reason=reason))
        self.state = new_state
        if new_state == "ADAPTATION":
            self.adaptation_observations = 0
            self.shadow_validator = ShadowValidator()   # fresh episode -- no stale pass streak

    def evaluate(self, perf_report: dict, feature_report: dict, prediction_report: dict,
                 *, candidate_window: dict | None = None) -> dict:
        evidence = compute_evidence(perf_report, feature_report, prediction_report)
        self.history.append({"ts": _now_iso(), "evidence": evidence.to_dict(), "state": self.state})

        if self.state == "ADAPTATION":
            self.adaptation_observations += 1
            if self.adaptation_observations >= ADAPTATION_MIN_OBSERVATIONS:
                self._transition("VALIDATION", evidence,
                                  f"{self.adaptation_observations} adaptation observations collected "
                                  f">= ADAPTATION_MIN_OBSERVATIONS {ADAPTATION_MIN_OBSERVATIONS}")

        elif self.state == "NORMAL":
            if evidence.total_score >= WATCH_MIN:
                self._transition("WATCH", evidence, f"score {evidence.total_score} >= WATCH_MIN {WATCH_MIN}")

        elif self.state == "WATCH":
            recent = self._recent_scores(DEGRADING_STREAK)
            if len(recent) >= DEGRADING_STREAK and all(s >= DEGRADING_MIN for s in recent):
                self._transition("DEGRADING", evidence,
                                  f"score >= {DEGRADING_MIN} for {DEGRADING_STREAK} consecutive evals")
            elif len(self._recent_scores(DEESCALATE_STREAK)) >= DEESCALATE_STREAK and \
                    all(s < WATCH_MIN for s in self._recent_scores(DEESCALATE_STREAK)):
                self._transition("NORMAL", evidence, f"{DEESCALATE_STREAK} consecutive low-evidence evals")

        elif self.state == "DEGRADING":
            recent = self.history[-BREAK_STREAK:]
            scores_ok = len(recent) >= BREAK_STREAK and all(
                h["evidence"]["total_score"] >= BREAK_MIN for h in recent)
            categories_touched = set()
            for h in recent:
                categories_touched |= {c for c, v in h["evidence"]["category_scores"].items() if v > 0}
            if scores_ok and len(categories_touched) >= BREAK_MIN_CATEGORIES:
                self._transition("STRUCTURAL_BREAK", evidence,
                                  f"score >= {BREAK_MIN} for {BREAK_STREAK} evals across "
                                  f"{len(categories_touched)} categories: {sorted(categories_touched)}")
            elif len(self._recent_scores(DEESCALATE_STREAK)) >= DEESCALATE_STREAK and \
                    all(s < DEGRADING_MIN for s in self._recent_scores(DEESCALATE_STREAK)):
                self._transition("WATCH", evidence, f"{DEESCALATE_STREAK} consecutive low-evidence evals")

        elif self.state == "STRUCTURAL_BREAK":
            self._transition("ADAPTATION", evidence, "structural break confirmed -> begin adaptation")

        elif self.state == "VALIDATION":
            if self.shadow_validator is None:
                self.shadow_validator = ShadowValidator()
            if candidate_window is not None:
                old_window = perf_report.get("medium", {})
                self._last_shadow_result = self.shadow_validator.evaluate(old_window, candidate_window)
                if self._last_shadow_result["promote_now"]:
                    self._transition("RECOVERED", evidence,
                                      f"candidate cleared validation: "
                                      f"{self._last_shadow_result['comparison']['reason']}")
                elif self.shadow_validator.attempts >= VALIDATION_MAX_ATTEMPTS:
                    self._transition("ADAPTATION", evidence,
                                      f"candidate did not clear validation within "
                                      f"VALIDATION_MAX_ATTEMPTS {VALIDATION_MAX_ATTEMPTS} attempts "
                                      "-- reverting to adaptation for further collection/re-tuning")
            # else: no candidate data yet this call -- stay in VALIDATION, no decision forced

        elif self.state == "RECOVERED":
            pass  # terminal for this instance -- see module docstring

        return {
            "state": self.state,
            "evidence": evidence.to_dict(),
            "adaptation_observations": self.adaptation_observations if self.state == "ADAPTATION" else None,
            "shadow_validation": self._last_shadow_result if self.state in ("VALIDATION", "RECOVERED") else None,
            "n_evaluations": len(self.history),
        }
