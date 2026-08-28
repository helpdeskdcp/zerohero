"""
Delay-aware guardrails.

Broker REST and the market-data WebSocket both arrive with latency. We keep a
set of timestamps per trade / per runner tick and derive an age for each. If
the market feed is stale we stop *new* entries but keep monitoring what is
already open (a live position still needs its stop watched, on the last good
price, with an explicit uncertainty flag). If reconciliation is stale we freeze
new entries entirely.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _parse(ts) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts, tz=timezone.utc)
        except Exception:
            return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def age_sec(ts, now: Optional[datetime] = None) -> Optional[float]:
    d = _parse(ts)
    if d is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - d).total_seconds())


@dataclass
class Clocks:
    signal_ts: Optional[str] = None
    market_data_ts: Optional[str] = None          # age of the LTP we'd act on
    order_submit_ts: Optional[str] = None
    order_fill_ts: Optional[str] = None
    last_broker_confirm_ts: Optional[str] = None  # last successful order-status read
    last_position_sync_ts: Optional[str] = None
    last_reconcile_ts: Optional[str] = None

    def ages(self, now=None) -> dict:
        return {k: age_sec(v, now) for k, v in self.__dict__.items()}


@dataclass
class StalenessReport:
    allow_new_entries: bool
    feed_stale: bool
    reconcile_stale: bool
    ages: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)


def assess(clocks: Clocks, *, max_ltp_age: float = 20.0,
           max_reconcile_age: float = 90.0, now=None) -> StalenessReport:
    ages = clocks.ages(now)
    reasons = []

    ltp_age = ages.get("market_data_ts")
    feed_stale = ltp_age is not None and ltp_age > max_ltp_age
    if feed_stale:
        reasons.append(f"market data {ltp_age:.0f}s old (> {max_ltp_age:.0f}s) — no new entries")
    if ltp_age is None:
        reasons.append("no market-data timestamp — treat as unusable for new entries")

    rec_age = ages.get("last_reconcile_ts")
    reconcile_stale = rec_age is not None and rec_age > max_reconcile_age
    if reconcile_stale:
        reasons.append(f"last reconcile {rec_age:.0f}s old (> {max_reconcile_age:.0f}s) — freeze entries")

    allow = (not feed_stale) and (ltp_age is not None) and (not reconcile_stale)
    return StalenessReport(allow_new_entries=allow, feed_stale=feed_stale,
                           reconcile_stale=reconcile_stale, ages=ages, reasons=reasons)
