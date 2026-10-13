"""Event envelope shared by later gates (market data, candles, signals). Pure data, no I/O."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field

TYPES = ("TICK", "CANDLE", "SIGNAL", "PROVIDER_HEALTH", "TENANT_SIGNAL", "AUDIT")


@dataclass(frozen=True)
class PlatformEvent:
    type: str
    payload: dict
    tenant_id: str | None = None          # None = shared/master event; set = tenant-private
    ts: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self):
        if self.type not in TYPES:
            raise ValueError(f"unknown event type {self.type!r}")

    def visible_to(self, tenant_id: str) -> bool:
        """Shared events are visible to every tenant; private events only to their owner."""
        return self.tenant_id is None or self.tenant_id == tenant_id

    def as_dict(self) -> dict:
        return asdict(self)
