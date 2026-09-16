"""
StrategyVerifier -- the orchestrator. Ties config + both strategies +
conflict resolution + confirmation + the state machine together and
produces the exact audit dict the spec's own example shows, ready for the
API/dashboard.

RAW SIGNAL != TRADE SIGNAL: this is the one place that boundary is
enforced. Nothing upstream of `.verify()` is touched or trusted to have
already decided CE/PE -- it only ever receives a raw BUY/SELL/BULLISH/
BEARISH/None hint plus market features, and only THIS function's output
may ever be treated as ENTRY_READY.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .base_strategy import MarketFeatures, build_indicator_snapshot
from .ce_strategy import evaluate_ce
from .conflict import resolve
from .confirmation import check_confirmation
from .config import StrategyConfig
from .pe_strategy import evaluate_pe
from .state_machine import StrategyStateMachine


@dataclass
class VerificationResult:
    timestamp: str | None
    symbol: str
    raw_signal: str | None
    strategy_direction: str
    ce_score: float
    pe_score: float
    ce_confidence: float
    pe_confidence: float
    confidence: float
    status: str                     # decision.status | confirmation-derived state
    state: str                      # WATCH | SETUP | CONFIRMED | ENTRY_READY | INVALIDATED | NO_TRADE
    conditions_passed: list
    conditions_failed: list
    confirmation_blockers: list
    reason: str
    entry_allowed: bool

    def to_dict(self) -> dict:
        return asdict(self)


class StrategyVerifier:
    def __init__(self, config: StrategyConfig | None = None):
        self.cfg = config or StrategyConfig()
        self.state_machine = StrategyStateMachine()

    def verify(self, *, symbol: str, raw_signal: str | None, features: MarketFeatures,
              timestamp: str | None = None) -> VerificationResult:
        ind = build_indicator_snapshot(features, self.cfg)
        ce = evaluate_ce(features, self.cfg, ind=ind)
        pe = evaluate_pe(features, self.cfg, ind=ind)
        decision = resolve(raw_signal, ce, pe, self.cfg)
        direction = decision["direction"]

        confirmed = False
        blockers: list = []
        if direction != "NO_TRADE" and ind is not None:
            conf = check_confirmation(
                direction=direction, ce_score=ce.score, pe_score=pe.score, ind=ind,
                features=features, direction_history=self.state_machine.history_for(symbol), cfg=self.cfg)
            confirmed = conf["confirmed"]
            blockers = conf["blockers"]
        elif ind is None:
            blockers = ["insufficient_bar_history"]

        side_result = ce if direction == "CE" else (pe if direction == "PE" else None)
        entry_allowed = bool(direction != "NO_TRADE" and confirmed
                             and side_result is not None and side_result.entry_allowed)

        state = self.state_machine.update(symbol, direction=direction,
                                          valid=(side_result.valid if side_result else False),
                                          confirmed=confirmed)

        conditions_passed = side_result.conditions_passed if side_result else []
        conditions_failed = side_result.conditions_failed if side_result else (ce.conditions_failed + pe.conditions_failed)
        confidence = side_result.confidence if side_result else 0.0

        reason_parts = [decision["reason"]]
        if blockers:
            reason_parts.append("held at " + state + " (" + ", ".join(blockers) + ")")
        elif state == "ENTRY_READY":
            reason_parts.append("confirmed -> entry ready")

        return VerificationResult(
            timestamp=timestamp, symbol=symbol, raw_signal=raw_signal,
            strategy_direction=direction, ce_score=ce.score, pe_score=pe.score,
            ce_confidence=ce.confidence, pe_confidence=pe.confidence, confidence=confidence,
            status=decision["status"], state=state,
            conditions_passed=conditions_passed, conditions_failed=conditions_failed,
            confirmation_blockers=blockers, reason="; ".join(reason_parts), entry_allowed=entry_allowed,
        )
