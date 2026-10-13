"""Failover arbiter + tick gate. Angel One is the default; Dhan only after it is marked validated.

* switch away from a provider only after it has been UNHEALTHY for `fail_after_sec` and a validated
  alternative has been HEALTHY for `confirm_sec`; fail back to the default only after it has been
  HEALTHY for `recover_after_sec` AND the current provider has served `min_dwell_sec` (no flapping).
* if no eligible provider is healthy: data_available=False, trading_blocked=True.
* TickGate admits ticks ONLY from the active provider, rejects duplicates / late / pre-handover
  ticks, and signals a volume-baseline reset on the first tick after a handover so no candle ever
  mixes two providers' cumulative volumes or double counts.
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass, field

from .health import HEALTHY, ProviderHealth
from .types import Tick, validate


@dataclass(frozen=True)
class ArbiterPolicy:
    fail_after_sec: float = 5.0
    confirm_sec: float = 10.0
    recover_after_sec: float = 60.0
    min_dwell_sec: float = 120.0


@dataclass
class Decision:
    active: str | None
    data_available: bool
    trading_blocked: bool
    reasons: list
    handovers: list = field(default_factory=list)


class ProviderArbiter:
    def __init__(self, default: str = "ANGELONE", providers=("ANGELONE", "DHAN"), policy: ArbiterPolicy | None = None):
        self.default, self.providers, self.p = default, tuple(providers), policy or ArbiterPolicy()
        self.validated = {default: True}                     # secondary providers must be validated explicitly
        self.validation_evidence: dict = {}
        self.active: str | None = None
        self._since: float | None = None                     # when the current provider became active
        self._bad_since: dict = {}                           # provider -> first UNHEALTHY ts (continuous)
        self._good_since: dict = {}                          # provider -> first HEALTHY ts (continuous)
        self.handovers: list = []

    def mark_validated(self, name: str, evidence: str) -> None:
        """Called only after the provider's auth, subscription, mapping, freshness and recovery tests pass."""
        if not evidence:
            raise ValueError("validation evidence required")
        self.validated[name] = True
        self.validation_evidence[name] = evidence

    def update(self, healths: dict, now: float) -> Decision:
        for n in self.providers:
            h: ProviderHealth | None = healths.get(n)
            ok = bool(h and h.state == HEALTHY)
            if ok:
                self._good_since.setdefault(n, now)
                self._bad_since.pop(n, None)
            else:
                self._bad_since.setdefault(n, now)
                self._good_since.pop(n, None)
        reasons: list = []
        elig = [n for n in self.providers if self.validated.get(n)]

        def good_for(n, sec):
            return n in self._good_since and now - self._good_since[n] >= sec

        if self.active is None:
            for n in [self.default] + [x for x in elig if x != self.default]:
                if n in elig and good_for(n, self.p.confirm_sec):
                    self._switch(n, now, "selection: first eligible provider HEALTHY for confirm_sec")
                    break
        else:
            cur_bad = self.active in self._bad_since and now - self._bad_since[self.active] >= self.p.fail_after_sec
            if cur_bad:
                alts = [n for n in elig if n != self.active and good_for(n, self.p.confirm_sec)]
                if alts:
                    self._switch(alts[0], now, f"failover: {self.active} unhealthy "
                                 f"({','.join(healths[self.active].reasons) if self.active in healths and healths[self.active] else 'no health'})")
                else:
                    reasons.append(f"{self.active} unhealthy and no validated healthy alternative")
                    self.active, self._since = None, None
            elif (self.active != self.default and self.default in elig and good_for(self.default, self.p.recover_after_sec)
                  and self._since is not None and now - self._since >= self.p.min_dwell_sec):
                self._switch(self.default, now, "failback: default provider recovered")
        for n in self.providers:
            if n not in elig and healths.get(n) is not None:
                reasons.append(f"{n} not eligible (not validated)")
        available = self.active is not None
        if not available:
            reasons.append("NO_HEALTHY_PROVIDER")
        return Decision(self.active, available, not available, reasons, list(self.handovers))

    def _switch(self, to: str, now: float, why: str) -> None:
        self.handovers.append({"ts": now, "from": self.active, "to": to, "reason": why})
        self.active, self._since = to, now


@dataclass
class GateResult:
    accepted: bool
    reason: str
    reset_volume_baseline: bool = False
    price_discontinuity: bool = False


class TickGate:
    def __init__(self, active_provider_fn, *, late_tolerance_sec: float = 2.0, dedupe_size: int = 4096,
                 discontinuity_pct: float = 2.0, dup_recv_window_sec: float = 0.05):
        self._active = active_provider_fn
        self.late_tol, self.disc_pct = late_tolerance_sec, discontinuity_pct
        # Without a cumulative-volume counter a tick has no unique id and source timestamps are
        # second-resolution, so identical (ts, price, qty) trades are common and LEGITIMATE. Measured on
        # 312k real Dhan ticks: median gap between identical keys 1.0 s, only 31.5% within 50 ms. Such ticks
        # are therefore duplicates only if received within dup_recv_window_sec of the earlier one.
        self.dup_window = dup_recv_window_sec
        self._seen: OrderedDict = OrderedDict()
        self._dedupe_size = dedupe_size
        self._last_ts: dict = {}                         # instrument -> last accepted event ts
        self._last_px: dict = {}
        self._provider_of: dict = {}                     # instrument -> provider that last fed it
        self._boundary: dict = {}                        # instrument -> handover boundary ts
        self._needs_reset: set = set()                   # instruments whose next accepted tick resets the volume baseline
        self.counters = defaultdict(int)
        self.shadow_by_provider = defaultdict(int)       # accepted-by-nobody ticks from non-active providers

    def accept(self, t: Tick) -> GateResult:
        bad = validate(t)
        if bad:
            return self._rej(bad)
        active = self._active()
        if active is None:
            return self._rej("NO_ACTIVE_PROVIDER")
        if t.provider != active:
            self.shadow_by_provider[t.provider] += 1
            return self._rej("NOT_ACTIVE_PROVIDER")
        key = (t.provider, t.instrument, t.source_ts, t.price, t.cum_volume, t.qty)
        prior_recv = self._seen.get(key)
        if prior_recv is not None and (t.cum_volume is not None or abs(t.recv_ts - prior_recv) <= self.dup_window):
            return self._rej("DUPLICATE")
        ets = t.event_ts()
        inst = t.instrument
        prev = self._provider_of.get(inst)
        if prev is not None and prev != t.provider:             # first tick after a provider handover
            self._boundary[inst] = self._last_ts.get(inst, ets)
            self._needs_reset.add(inst)
            self._provider_of[inst] = t.provider
        bnd = self._boundary.get(inst)
        if bnd is not None and ets <= bnd:                      # would double count the previous provider's period
            return self._rej("BEFORE_HANDOVER_BOUNDARY")
        last = self._last_ts.get(inst)
        if last is not None and ets < last - self.late_tol:
            return self._rej("LATE")
        reset = inst in self._needs_reset
        disc = False
        lp = self._last_px.get(inst)
        if reset and lp:
            disc = abs(t.price / lp - 1.0) * 100.0 > self.disc_pct
        self._needs_reset.discard(inst)
        self._seen[key] = t.recv_ts
        self._seen.move_to_end(key)
        if len(self._seen) > self._dedupe_size:
            self._seen.popitem(last=False)
        self._last_ts[inst] = max(ets, last if last is not None else ets)
        self._last_px[inst] = t.price
        self._provider_of[inst] = t.provider
        self.counters["ACCEPTED"] += 1
        return GateResult(True, "OK", reset_volume_baseline=reset, price_discontinuity=disc)

    def _rej(self, reason) -> GateResult:
        self.counters[reason] += 1
        return GateResult(False, reason)
