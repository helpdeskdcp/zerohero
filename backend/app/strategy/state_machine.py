"""
Per-symbol state: WATCH -> SETUP -> CONFIRMED/ENTRY_READY, or INVALIDATED
when a building setup flips/dies before confirming. A setup NEVER jumps
straight from nothing to ENTRY_READY in one call -- confirmation.py's
persistence check (confirmation_min_count consecutive same-direction reads)
gates that transition.
"""
from __future__ import annotations

from dataclasses import dataclass, field

STATES = ("WATCH", "SETUP", "CONFIRMED", "ENTRY_READY", "INVALIDATED", "NO_TRADE")


@dataclass
class _SymbolRecord:
    direction_history: list = field(default_factory=list)   # most recent directions, oldest..newest
    state: str = "NO_TRADE"
    last_direction: str | None = None


class StrategyStateMachine:
    """Holds no market data -- only the small per-symbol memory needed to
    tell a fresh reading apart from a persisting one. Safe to keep for the
    life of a process; a fresh instance per backtest run keeps runs
    independent (no cross-run leakage)."""

    def __init__(self, *, history_len: int = 10):
        self._history_len = history_len
        self._records: dict[str, _SymbolRecord] = {}

    def _rec(self, symbol: str) -> _SymbolRecord:
        return self._records.setdefault(symbol, _SymbolRecord())

    def history_for(self, symbol: str) -> list:
        return list(self._rec(symbol).direction_history)

    def update(self, symbol: str, *, direction: str, valid: bool, confirmed: bool) -> str:
        rec = self._rec(symbol)
        was_building = rec.state in ("WATCH", "SETUP", "CONFIRMED", "ENTRY_READY")

        if direction == "NO_TRADE" or not valid:
            new_state = "INVALIDATED" if was_building else "NO_TRADE"
        elif confirmed:
            new_state = "ENTRY_READY"
        elif direction == rec.last_direction and rec.state in ("WATCH", "SETUP"):
            new_state = "SETUP"          # persisting, still gathering confirmation
        else:
            new_state = "WATCH"          # freshly appeared this direction

        rec.direction_history.append(direction)
        rec.direction_history = rec.direction_history[-self._history_len:]
        rec.last_direction = direction if direction != "NO_TRADE" else None
        rec.state = new_state
        return new_state

    def reset(self, symbol: str | None = None) -> None:
        if symbol is None:
            self._records.clear()
        else:
            self._records.pop(symbol, None)
