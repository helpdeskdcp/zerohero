"""Tick model + validation. Provenance (provider, source_ts, recv_ts) is always preserved."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Tick:
    provider: str                 # "ANGELONE" | "DHAN"
    instrument: str               # provider-neutral key, e.g. "NSE:99926000"
    price: float
    source_ts: float | None       # exchange/provider timestamp (epoch s); None if the provider sent none
    recv_ts: float                # our wall clock at receipt
    qty: float | None = None      # last traded quantity if supplied
    cum_volume: float | None = None   # cumulative day volume if supplied (NOT per-tick volume)
    oi: float | None = None       # OI snapshot (never summed)

    def event_ts(self) -> float:
        """Timestamp used for ordering/bucketing: provider time when available, else receipt time."""
        return self.source_ts if self.source_ts is not None else self.recv_ts


def validate(t: Tick, max_future_sec: float = 5.0, max_age_sec: float = 3600.0) -> str | None:
    """Return a rejection reason, or None if the tick is structurally valid."""
    if not (isinstance(t.price, (int, float)) and math.isfinite(t.price) and t.price > 0):
        return "BAD_PRICE"
    if t.source_ts is not None:
        if not math.isfinite(t.source_ts) or t.source_ts <= 0:
            return "BAD_SOURCE_TS"
        if t.source_ts > t.recv_ts + max_future_sec:
            return "SOURCE_TS_IN_FUTURE"
        if t.recv_ts - t.source_ts > max_age_sec:
            return "SOURCE_TS_TOO_OLD"
    for name in ("qty", "cum_volume", "oi"):
        v = getattr(t, name)
        if v is not None and (not math.isfinite(v) or v < 0):
            return f"BAD_{name.upper()}"
    return None
