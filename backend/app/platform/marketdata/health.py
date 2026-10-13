"""Provider health: HEALTHY only when authentication, subscription AND recent valid data all pass.

An open socket is never evidence of health. Authentication that cannot be proven by the token
clock (UNCERTAIN/UNKNOWN) is accepted only if fresh data is demonstrably arriving; EXPIRED /
MISSING / REJECTED auth is unhealthy regardless of the socket.
"""
from __future__ import annotations

from dataclasses import dataclass, field

HEALTHY, DEGRADED, UNHEALTHY, IDLE = "HEALTHY", "DEGRADED", "UNHEALTHY", "IDLE_MARKET_CLOSED"
BAD_AUTH = {"EXPIRED", "MISSING", "REJECTED_OR_NO_DATA"}


@dataclass(frozen=True)
class HealthPolicy:
    stale_after_sec: float = 12.0          # same freshness rule the live execution path uses
    min_fresh_fraction: float = 0.8        # share of REQUIRED instruments that must be fresh
    max_reconnects_window: int = 5         # more reconnects than this in the window => DEGRADED
    stale_message_after_sec: float = 30.0  # no message at all for this long => unhealthy


@dataclass
class ProviderFacts:
    name: str
    auth_state: str = "UNKNOWN"            # VALID | UNCERTAIN | UNKNOWN | EXPIRED | MISSING | REJECTED_OR_NO_DATA
    socket_open: bool = False
    subscribed: bool = False
    last_message_ts: float | None = None
    instrument_last_ts: dict = field(default_factory=dict)   # instrument -> last valid tick (recv) epoch
    reconnects_in_window: int = 0
    message_rate_per_min: float | None = None
    last_error: str | None = None


@dataclass
class ProviderHealth:
    name: str
    state: str
    reasons: list
    fresh: list
    stale: list
    checked_ts: float

    @property
    def healthy(self) -> bool:
        return self.state == HEALTHY


def evaluate(f: ProviderFacts, required: list[str], now: float, policy: HealthPolicy | None = None) -> ProviderHealth:
    p = policy or HealthPolicy()
    if not required:
        return ProviderHealth(f.name, IDLE, ["no required instruments (market closed / none configured)"], [], [], now)
    fresh = [i for i in required if (now - f.instrument_last_ts.get(i, -1e18)) <= p.stale_after_sec]
    stale = [i for i in required if i not in fresh]
    frac = len(fresh) / len(required)
    reasons: list = []
    if f.auth_state in BAD_AUTH:
        reasons.append(f"AUTH_{f.auth_state}")
    elif f.auth_state in ("UNCERTAIN", "UNKNOWN") and frac < p.min_fresh_fraction:
        reasons.append(f"AUTH_{f.auth_state}_UNPROVEN_BY_DATA")
    if not f.socket_open:
        reasons.append("SOCKET_CLOSED")
    if not f.subscribed:
        reasons.append("NOT_SUBSCRIBED")
    if f.last_message_ts is None or now - f.last_message_ts > p.stale_message_after_sec:
        reasons.append("NO_RECENT_MESSAGES")
    if frac == 0:
        reasons.append("NO_FRESH_INSTRUMENT_DATA")
    if reasons:
        return ProviderHealth(f.name, UNHEALTHY, reasons, fresh, stale, now)
    if frac < p.min_fresh_fraction:
        return ProviderHealth(f.name, DEGRADED, [f"ONLY_{len(fresh)}_OF_{len(required)}_INSTRUMENTS_FRESH"], fresh, stale, now)
    if f.reconnects_in_window > p.max_reconnects_window:
        return ProviderHealth(f.name, DEGRADED, [f"RECONNECT_STORM_{f.reconnects_in_window}"], fresh, stale, now)
    return ProviderHealth(f.name, HEALTHY, [], fresh, stale, now)


def facts_from_angel_status(st: dict, now: float, name: str = "ANGELONE", token_prefix: str = "") -> ProviderFacts:
    """Adapter over AngelMarketFeed.status() (its additive truthful fields)."""
    marks = st.get("marks") or {}
    last = {f"{token_prefix}{tok}": now - float(m.get("age_sec", 1e9)) for tok, m in marks.items()}
    msg_age = st.get("last_msg_age_sec")
    return ProviderFacts(name=name, auth_state="UNKNOWN" if st.get("socket_open") else "UNKNOWN",
                         socket_open=bool(st.get("socket_open", st.get("connected"))),
                         subscribed=bool(st.get("subscribed")), instrument_last_ts=last,
                         last_message_ts=None if msg_age is None else now - float(msg_age),
                         last_error=st.get("last_error"))


def facts_from_dhan_status(st: dict, now: float, last_ts_by_instrument: dict, name: str = "DHAN") -> ProviderFacts:
    """Adapter over DhanOrderflowWorker.status(); auth_state comes from the token clock/close behaviour."""
    msg_age = st.get("last_msg_age_sec")
    return ProviderFacts(name=name, auth_state=st.get("auth_state", "UNKNOWN"),
                         socket_open=bool(st.get("socket_open")), subscribed=bool(st.get("socket_open") and st.get("frames")),
                         instrument_last_ts=dict(last_ts_by_instrument),
                         last_message_ts=None if msg_age is None else now - float(msg_age),
                         reconnects_in_window=int(st.get("reconnects", 0)), last_error=st.get("last_error"))
