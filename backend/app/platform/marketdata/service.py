"""Glue (not wired into the live app): health -> arbiter -> gate -> 1s engine -> higher timeframes -> writer.

All time comes from the caller (`now`), so behaviour is deterministic in tests. Calling code feeds ticks via
on_tick() and periodically calls evaluate(). When no provider is healthy, data is flagged unavailable and
`trading_blocked` is True: downstream signal/order code must not trade on stale data.
"""
from __future__ import annotations

from ..candles.engine_1s import CandleEngine1s
from ..candles.timeframes import MultiTF
from .arbiter import ProviderArbiter, TickGate
from .health import HealthPolicy, ProviderFacts, evaluate
from .types import Tick


class MarketDataService:
    def __init__(self, writer=None, *, arbiter: ProviderArbiter | None = None, engine: CandleEngine1s | None = None,
                 health_policy: HealthPolicy | None = None, tfs=(5, 15, 30, 60, 300, 900, 1800, 3600)):
        self.arbiter = arbiter or ProviderArbiter()
        self.gate = TickGate(lambda: self.arbiter.active)
        self.engine = engine or CandleEngine1s()
        self.multi = MultiTF(tfs=tfs)
        self.writer = writer
        self.policy = health_policy or HealthPolicy()
        self.decision = None
        self.healths: dict = {}
        self.dropped_unmapped = 0
        self._gap_marker = None
        self.subscribers: list = []              # callables(candle) for live push (delta updates)

    def on_tick(self, tick: Tick | None) -> list:
        if tick is None:
            self.dropped_unmapped += 1
            return []
        r = self.gate.accept(tick)
        if not r.accepted:
            return []
        return self._emit(self.engine.add(tick, r.reset_volume_baseline))

    def tick(self, now: float) -> list:
        """Timer: close candles that can no longer receive ticks."""
        return self._emit(self.engine.flush(now))

    def evaluate(self, facts: dict, required: dict, now: float):
        """facts: provider -> ProviderFacts; required: provider -> [instrument keys needing fresh data]."""
        self.healths = {n: evaluate(f, required.get(n, []), now, self.policy) for n, f in facts.items()}
        self.decision = self.arbiter.update(self.healths, now)
        return self.decision

    def _persist_new_gaps(self) -> None:
        new = []
        for g in reversed(self.engine.gaps):
            if g is self._gap_marker:
                break
            new.append(g)
        if new:
            self._gap_marker = self.engine.gaps[-1]
            self.writer.write_gaps(list(reversed(new)))

    @property
    def trading_blocked(self) -> bool:
        return True if self.decision is None else self.decision.trading_blocked

    def partials(self, instrument: str) -> list:
        """Forming (closed=False) higher-timeframe candles for live charts; never persisted."""
        return [p for tf in self.multi.tfs if (p := self.multi.current(instrument, tf)) is not None]

    def _emit(self, closed: list) -> list:
        out = []
        for c in closed:
            out.append(c)
            out += self.multi.add(c)
        if self.writer:
            for c in out:
                self.writer.offer(c)
            self._persist_new_gaps()
        for c in out:
            for s in self.subscribers:
                s(c)
        return out
