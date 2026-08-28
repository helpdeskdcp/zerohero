"""
Broker abstraction — the contract every execution backend implements.

The Signal Engine and Risk Engine NEVER see this module. They produce a
contract dict; the OrderManager turns it into a TradeState and drives a
BrokerBase. Swapping PAPER / SHADOW / LIVE swaps only the BrokerBase impl.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


class LiveDisabled(RuntimeError):
    """Raised by AngelOneBroker order methods when live trading is not fully
    enabled (mode + env flag + confirm token). Never subclassed away."""


# ---------------------------------------------------------------- vocab
class Side:
    BUY = "BUY"
    SELL = "SELL"

    @staticmethod
    def opposite(s: str) -> str:
        return Side.SELL if s == Side.BUY else Side.BUY


class OrderType:
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"          # stop-loss limit  (price + trigger)
    SL_M = "SL-M"      # stop-loss market (trigger only)


class Leg:
    ENTRY = "ENTRY"
    TARGET = "TARGET"
    SL = "SL"
    EXIT = "EXIT"


class OStatus:
    """Normalised order status — the ONLY status vocabulary the rest of the
    adapter reasons about. Broker-specific strings are mapped into this."""
    PREARMED = "PREARMED"      # intent persisted, nothing sent
    ACCEPTED = "ACCEPTED"      # broker acknowledged receipt (NOT a fill)
    OPEN = "OPEN"             # working at the exchange, unfilled
    PARTIAL = "PARTIAL"       # some quantity filled, rest still working / cancelled
    COMPLETE = "COMPLETE"     # fully filled
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"       # could not determine — DO NOT act, reconcile

    TERMINAL = frozenset({COMPLETE, REJECTED, CANCELLED})
    LIVE = frozenset({ACCEPTED, OPEN, PARTIAL, COMPLETE})   # "already out there"


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- data objects
@dataclass
class OrderReq:
    """What the OrderManager hands a broker. Broker-neutral."""
    client_tag: str
    trade_id: str
    leg: str                       # Leg.*
    side: str                      # Side.*
    order_type: str                # OrderType.*
    symbol: str
    symboltoken: str
    exchange: str
    quantity: float
    tradingsymbol: str = ""
    product: str = "INTRADAY"
    variety: str = "NORMAL"
    limit_price: Optional[float] = None
    trigger_price: Optional[float] = None
    # provenance (audit / staleness only — never affects routing)
    signal_confidence: Optional[float] = None
    signal_ts: Optional[str] = None
    market_data_ts: Optional[str] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class OrderAck:
    """Return of every submit call. `ok` means the broker ACKNOWLEDGED the
    request — it is NEVER proof of a fill. `ambiguous` means the call failed in
    a way where the order may still have reached the broker: the caller must
    reconcile before any retry."""
    ok: bool
    client_tag: str = ""
    broker_order_id: str = ""
    unique_order_id: str = ""
    status: str = OStatus.UNKNOWN
    ambiguous: bool = False
    error: str = ""
    raw: dict = field(default_factory=dict)
    ts: str = field(default_factory=_now_iso)


@dataclass
class OrderStatusResult:
    status: str = OStatus.UNKNOWN          # OStatus.*
    filled_qty: float = 0.0
    pending_qty: Optional[float] = None
    avg_price: Optional[float] = None
    broker_order_id: str = ""
    unique_order_id: str = ""
    text: str = ""                          # broker status/rejection text
    raw: dict = field(default_factory=dict)
    ts: str = field(default_factory=_now_iso)


@dataclass
class BrokerPosition:
    symbol: str
    symboltoken: str
    exchange: str
    net_qty: float               # signed
    avg_price: Optional[float]
    ltp: Optional[float] = None
    option_type: str = ""
    strike: Optional[float] = None
    product: str = ""


@dataclass
class PositionSnapshot:
    ok: bool
    positions: list = field(default_factory=list)   # list[BrokerPosition]
    error: str = ""
    ts: str = field(default_factory=_now_iso)

    def by_token(self, token: str) -> Optional[BrokerPosition]:
        for p in self.positions:
            if str(p.symboltoken) == str(token):
                return p
        return None


# ---------------------------------------------------------------- broker status mapping
_ANGEL_STATUS_MAP = {
    "complete": OStatus.COMPLETE,
    "rejected": OStatus.REJECTED,
    "cancelled": OStatus.CANCELLED,
    "canceled": OStatus.CANCELLED,
    "open": OStatus.OPEN,
    "open pending": OStatus.OPEN,
    "trigger pending": OStatus.OPEN,
    "validation pending": OStatus.OPEN,
    "put order req received": OStatus.OPEN,
    "modify pending": OStatus.OPEN,
    "modified": OStatus.OPEN,
    "after market order req received": OStatus.OPEN,
    "cancelled after market order": OStatus.CANCELLED,
}


def map_broker_status(text: str, requested_qty=None, filled_qty=None) -> str:
    """Broker status string (+ quantities) -> OStatus. Partial is inferred:
    a still-'open' order with some fill, or a 'cancelled' order that had a fill."""
    base = _ANGEL_STATUS_MAP.get(str(text or "").strip().lower(), OStatus.UNKNOWN)
    rq, fq = _f(requested_qty), _f(filled_qty)
    if fq and rq and 0 < fq < rq:
        if base in (OStatus.OPEN, OStatus.CANCELLED, OStatus.UNKNOWN):
            return OStatus.PARTIAL
    if base == OStatus.COMPLETE and fq is not None and rq is not None and fq < rq:
        return OStatus.PARTIAL
    return base


# ---------------------------------------------------------------- ABC
class BrokerBase:
    """Abstract. Every method returns a dataclass above (never a raw dict) and,
    except for the LIVE guard's LiveDisabled, never raises for expected failure
    modes — it returns ok=False / status=UNKNOWN so the caller can reconcile."""

    name = "base"
    mode = "BASE"

    # -- session --------------------------------------------------------------
    def login(self) -> dict:
        raise NotImplementedError

    def refresh_session(self) -> dict:
        raise NotImplementedError

    def logout(self) -> dict:
        raise NotImplementedError

    # -- entries ------------------------------------------------------------
    def market_entry(self, req: OrderReq) -> OrderAck:
        raise NotImplementedError

    def limit_entry(self, req: OrderReq) -> OrderAck:
        raise NotImplementedError

    # -- protective / exit orders ----------------------------------------
    def stoploss_market(self, req: OrderReq) -> OrderAck:
        raise NotImplementedError

    def stoploss_limit(self, req: OrderReq) -> OrderAck:
        raise NotImplementedError

    def target_exit(self, req: OrderReq) -> OrderAck:
        raise NotImplementedError

    # -- lifecycle --------------------------------------------------------
    def modify_order(self, req: OrderReq, **changes) -> OrderAck:
        raise NotImplementedError

    def cancel_order(self, client_tag: str, broker_order_id: str = "",
                     variety: str = "NORMAL") -> OrderAck:
        raise NotImplementedError

    # -- read (idempotent, safe to retry) ------------------------------
    def get_order_status(self, broker_order_id: str = "", unique_order_id: str = "",
                         client_tag: str = "") -> OrderStatusResult:
        """`client_tag` is the fallback key when a submit was ambiguous and no
        broker id was returned — the broker matches it against the order tag."""
        raise NotImplementedError

    def get_order_book(self) -> list:
        raise NotImplementedError

    def get_positions(self) -> PositionSnapshot:
        raise NotImplementedError

    def reconcile_position(self, symboltoken: str) -> Optional[BrokerPosition]:
        snap = self.get_positions()
        return snap.by_token(symboltoken) if snap.ok else None
