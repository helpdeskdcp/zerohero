"""
Trade Pre-Arm.

The complete trade is prepared LOCALLY before a single byte goes to the broker:
instrument, side, quantity, entry type, expected entry, target, stop, trailing
rules, strategy, confidence and both timestamps (signal + market data). Target
and stop are computed here, up front — the monitor does not wait for a broker
confirmation to know what it is watching for.

`TradeState` is broker-neutral. Prices are provisional (anchored to the
expected entry) until `apply_fill()` re-anchors them to the actual average
fill price.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from .broker_base import Side, OrderType, Leg, OStatus, OrderReq
from .idempotency import tag as _tag


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _now():
    return datetime.now(timezone.utc).isoformat()


class PrearmRejected(ValueError):
    """The contract cannot become a valid trade (bad side, qty<=0, stop on the
    wrong side of entry, etc.). Raised by TradeState.from_contract."""


@dataclass
class StopPlan:
    price: float
    kind: str = "FIXED"                 # FIXED | ATR | TRAIL
    distance: Optional[float] = None    # absolute points from reference (for TRAIL / re-anchor)
    atr: Optional[float] = None
    atr_mult: Optional[float] = None
    trail_trigger: Optional[float] = None   # arm the trail once favourable excursion >= this
    breakeven_trigger: Optional[float] = None
    profit_lock: Optional[float] = None     # once armed, keep locking (excursion - this)


@dataclass
class TargetPlan:
    price: float
    distance: Optional[float] = None
    price_2: Optional[float] = None
    rr: Optional[float] = None


@dataclass
class TradeState:
    # -- identity --
    trade_id: str
    strategy: str = "SCALP"
    # -- instrument --
    symbol: str = ""
    symboltoken: str = ""
    exchange: str = ""
    tradingsymbol: str = ""
    product: str = "INTRADAY"
    option_type: str = ""
    strike: Optional[float] = None
    expiry: str = ""
    underlying: str = ""
    # -- order --
    side: str = Side.BUY               # BUY | SELL
    quantity: float = 0.0
    entry_type: str = OrderType.MARKET  # MARKET | LIMIT
    limit_price: Optional[float] = None
    expected_entry_price: Optional[float] = None
    # -- pre-calculated plan --
    target: Optional[TargetPlan] = None
    stop: Optional[StopPlan] = None
    max_hold_sec: Optional[float] = None
    # -- provenance --
    signal_confidence: Optional[float] = None
    signal_ts: Optional[str] = None
    market_data_ts: Optional[str] = None
    prearm_ts: str = field(default_factory=_now)
    # -- mutable fill state (broker is source of truth) --
    status: str = OStatus.PREARMED
    filled_qty: float = 0.0
    avg_fill_price: Optional[float] = None
    broker_order_id: str = ""
    unique_order_id: str = ""

    # ------------------------------------------------------------------ build
    @classmethod
    def from_contract(cls, contract: dict, *, instrument: dict | None = None,
                      quantity: float | None = None,
                      default_stop_pct: float = 0.35,
                      default_target_rr: float = 1.6) -> "TradeState":
        """Turn a Signal/Scalp pipeline contract into a validated TradeState.

        The contract already carries entry_ref / target_1 / target_2 / stop_loss
        / trailing_stop / atr_pct — we snapshot those. Missing target or stop is
        derived from entry ± (atr_pct% or default_stop_pct%) so pre-arm always
        produces a complete plan."""
        instrument = instrument or {}
        c = contract or {}

        direction = (c.get("direction") or "").upper()
        # option BUY-only semantics: a CE/PE trade is always a BUY of the option;
        # for a futures/equity contract, direction is the order side.
        opt = (c.get("option_type") or instrument.get("option_type") or "").upper()
        side = Side.BUY if opt in ("CE", "PE") else (
            Side.BUY if direction == "BUY" else Side.SELL if direction == "SELL" else "")
        if side not in (Side.BUY, Side.SELL):
            raise PrearmRejected(f"no actionable side (direction={direction!r}, option={opt!r})")

        qty = _f(quantity if quantity is not None else c.get("allowed_quantity") or c.get("quantity"))
        if not qty or qty <= 0:
            raise PrearmRejected(f"quantity must be > 0 (got {qty!r})")

        entry = _f(c.get("entry_ref")) or _f(c.get("entry"))
        if entry is None or entry <= 0:
            raise PrearmRejected("entry reference missing/invalid — cannot pre-arm")

        atr_pct = _f(c.get("atr_pct"))
        stop_pct = atr_pct if (atr_pct and atr_pct > 0) else default_stop_pct
        # BUY: stop below, target above.  SELL: stop above, target below.
        sgn = 1.0 if side == Side.BUY else -1.0

        stop_px = _f(c.get("stop_loss"))
        if stop_px is None or stop_px <= 0:
            stop_px = round(entry * (1 - sgn * stop_pct / 100.0), 2)
        stop_dist = abs(entry - stop_px)
        if stop_dist <= 0:
            raise PrearmRejected("zero stop distance")
        if side == Side.BUY and stop_px >= entry:
            raise PrearmRejected("BUY stop not below entry")
        if side == Side.SELL and stop_px <= entry:
            raise PrearmRejected("SELL stop not above entry")

        tgt_px = _f(c.get("target_1"))
        if tgt_px is None or tgt_px <= 0:
            tgt_px = round(entry + sgn * stop_dist * default_target_rr, 2)
        if side == Side.BUY and tgt_px <= entry:
            raise PrearmRejected("BUY target not above entry")
        if side == Side.SELL and tgt_px >= entry:
            raise PrearmRejected("SELL target not above entry (should be below)")

        trail = _f(c.get("trailing_stop")) or None

        target = TargetPlan(
            price=round(tgt_px, 2), distance=round(abs(tgt_px - entry), 4),
            price_2=_f(c.get("target_2")) or None,
            rr=round(abs(tgt_px - entry) / stop_dist, 2) if stop_dist else None)
        stop = StopPlan(
            price=round(stop_px, 2), kind="ATR" if atr_pct else "FIXED",
            distance=round(stop_dist, 4), atr=None,
            atr_mult=None,
            trail_trigger=trail, breakeven_trigger=stop_dist, profit_lock=trail)

        entry_type = OrderType.LIMIT if (c.get("entry_type") or "").upper() == "LIMIT" else OrderType.MARKET

        return cls(
            trade_id=c.get("trade_id") or c.get("signal_id") or _fallback_id(),
            strategy=(c.get("strategy") or "SCALP").upper(),
            symbol=c.get("symbol") or c.get("underlying") or instrument.get("symbol") or "",
            symboltoken=str(c.get("symboltoken") or instrument.get("symboltoken") or ""),
            exchange=(c.get("market") or c.get("exchange") or instrument.get("exchange") or "").upper(),
            tradingsymbol=c.get("tradingsymbol") or instrument.get("tradingsymbol") or "",
            product=(instrument.get("product") or "INTRADAY").upper(),
            option_type=opt, strike=_f(c.get("strike")), expiry=c.get("expiry") or "",
            underlying=c.get("underlying") or c.get("symbol") or "",
            side=side, quantity=qty, entry_type=entry_type,
            limit_price=round(entry, 2) if entry_type == OrderType.LIMIT else None,
            expected_entry_price=round(entry, 2),
            target=target, stop=stop, max_hold_sec=_f(c.get("max_hold_sec")),
            signal_confidence=_f(c.get("confidence")),
            signal_ts=c.get("created_ts") or c.get("signal_ts"),
            market_data_ts=c.get("market_data_ts") or c.get("data_fetched_at"),
        )

    # ------------------------------------------------------------------ fills
    def apply_fill(self, filled_qty: float, avg_price: Optional[float], *, complete: bool):
        """Broker fill info arrived. Re-anchor target & stop distances to the
        ACTUAL average fill price (spec §7) and switch quantity to what was
        really filled (spec §5). No-op if we get nothing usable."""
        fq = _f(filled_qty)
        if fq is not None and fq >= 0:
            self.filled_qty = fq
        ap = _f(avg_price)
        if ap and ap > 0:
            prev_ref = self.avg_fill_price or self.expected_entry_price or ap
            self.avg_fill_price = round(ap, 2)
            if self.target and self.target.distance:
                sgn = 1.0 if self.side == Side.BUY else -1.0
                self.target.price = round(ap + sgn * self.target.distance, 2)
            if self.stop and self.stop.distance:
                sgn = 1.0 if self.side == Side.BUY else -1.0
                # only re-anchor if the stop has NOT already been trailed past entry
                trailed = (self.side == Side.BUY and self.stop.price > prev_ref) or \
                          (self.side == Side.SELL and self.stop.price < prev_ref)
                if not trailed:
                    self.stop.price = round(ap - sgn * self.stop.distance, 2)
        self.status = OStatus.COMPLETE if complete else (
            OStatus.PARTIAL if (self.filled_qty or 0) > 0 else self.status)

    # ------------------------------------------------------------------ views
    @property
    def is_provisional(self) -> bool:
        """True until the broker has confirmed an average fill price."""
        return self.avg_fill_price is None

    @property
    def monitor_qty(self) -> float:
        """Quantity the monitor should size exits from: NEVER assume the full
        request filled — use confirmed filled qty once we have any."""
        if self.filled_qty and self.filled_qty > 0:
            return self.filled_qty
        return self.quantity if self.is_provisional else 0.0

    @property
    def entry_ref(self) -> float:
        return self.avg_fill_price or self.expected_entry_price or 0.0

    def order_req(self, leg: str, *, order_type: str, side: str | None = None,
                  quantity: float | None = None, limit_price=None,
                  trigger_price=None) -> OrderReq:
        return OrderReq(
            client_tag=_tag(self.trade_id, leg), trade_id=self.trade_id, leg=leg,
            side=side or self.side, order_type=order_type,
            symbol=self.symbol, symboltoken=self.symboltoken, exchange=self.exchange,
            tradingsymbol=self.tradingsymbol, product=self.product,
            quantity=quantity if quantity is not None else self.quantity,
            limit_price=limit_price, trigger_price=trigger_price,
            signal_confidence=self.signal_confidence, signal_ts=self.signal_ts,
            market_data_ts=self.market_data_ts)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["is_provisional"] = self.is_provisional
        d["monitor_qty"] = self.monitor_qty
        d["entry_ref"] = self.entry_ref
        return d


def _fallback_id() -> str:
    import random
    return "TRD-" + format(int(datetime.now(timezone.utc).timestamp() * 1000), "x") \
        + "-" + format(random.randint(0, 0xFFFFF), "x")
