"""
AngelOneBroker — BrokerBase over the SmartAPI order REST (connectors/angelone_orders).

LIVE order placement is triple-gated. `market_entry` / `limit_entry` /
`stoploss_*` / `target_exit` / `modify_order` / `cancel_order` raise
`LiveDisabled` unless ALL of:

    * config["execution_mode"] == "LIVE"
    * os.environ["CHANAKYA_ALLOW_LIVE"] == "1"
    * os.environ["CHANAKYA_LIVE_CONFIRM_TOKEN"] is a non-empty string

Read methods (`get_order_status`, `get_order_book`, `get_positions`,
`reconcile_position`, `login`, `refresh_session`) work in any mode — they are
safe and let SHADOW mode reconcile against the real account.

Submission is rate-limited and circuit-broken but NEVER auto-retried: a
transient failure returns an OrderAck with `ambiguous=True` and the
OrderManager reconciles before deciding anything.
"""
from __future__ import annotations

import os

from ..connectors import angelone, angelone_orders
from .broker_base import (
    BrokerBase, LiveDisabled, OrderReq, OrderAck, OrderStatusResult,
    PositionSnapshot, BrokerPosition, Side, OrderType, OStatus, map_broker_status,
)
from .ratelimit import TokenBucket, CircuitBreaker, call_with_retry

_ORDER_TYPE_MAP = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.SL: "STOPLOSS_LIMIT",
    OrderType.SL_M: "STOPLOSS_MARKET",
}


class AngelOneBroker(BrokerBase):
    name = "angelone"

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.mode = (cfg.get("execution_mode") or "PAPER").upper()
        # Never accept this secret from API-persisted runtime configuration.
        # It must exist only in the process environment of the deployment.
        self._confirm = (os.environ.get("CHANAKYA_LIVE_CONFIRM_TOKEN") or "").strip()
        self._bucket = TokenBucket(cfg.get("exec_rate_per_sec", 3), cfg.get("exec_burst", 5))
        self._breaker = CircuitBreaker(cfg.get("exec_fail_threshold", 4),
                                       cfg.get("exec_breaker_reset_sec", 30))
        self._read_retries = int(cfg.get("exec_max_retries", 2))

    # ---------------------------------------------------------------- live gate
    @property
    def live_enabled(self) -> bool:
        return (self.mode == "LIVE"
                and os.environ.get("CHANAKYA_ALLOW_LIVE") == "1"
                and bool(self._confirm))

    def _guard_live(self, what: str):
        if not self.live_enabled:
            why = []
            if self.mode != "LIVE":
                why.append(f"execution_mode={self.mode}")
            if os.environ.get("CHANAKYA_ALLOW_LIVE") != "1":
                why.append("env CHANAKYA_ALLOW_LIVE!=1")
            if not self._confirm:
                why.append("env CHANAKYA_LIVE_CONFIRM_TOKEN empty")
            raise LiveDisabled(f"{what} blocked — live trading not fully enabled ({', '.join(why)})")

    def _rate(self):
        if not self._breaker.allow():
            return OrderAck(ok=False, ambiguous=False, error="circuit breaker open — broker calls paused")
        if not self._bucket.take(1, max_wait=3.0):
            return OrderAck(ok=False, ambiguous=False, error="rate limit — no slot")
        return None

    # ---------------------------------------------------------------- session
    def login(self):
        st, _, err = angelone._login()
        return {"status": st, "error": err}

    def refresh_session(self):
        st, _, err = angelone._get_jwt()
        return {"status": st, "error": err}

    def logout(self):
        return angelone_orders.logout()

    # ---------------------------------------------------------------- submit
    def _submit(self, req: OrderReq, *, is_exit: bool) -> OrderAck:
        self._guard_live("order submission")
        blocked = self._rate()
        if blocked is not None:
            return blocked
        body = {
            "variety": req.variety or "NORMAL",
            "tradingsymbol": req.tradingsymbol or req.symbol,
            "symboltoken": str(req.symboltoken),
            "transactiontype": req.side,
            "exchange": req.exchange,
            "ordertype": _ORDER_TYPE_MAP.get(req.order_type, "MARKET"),
            "producttype": req.product or "INTRADAY",
            "duration": "DAY",
            "quantity": str(int(req.quantity)),
            "price": str(req.limit_price if req.limit_price is not None else 0),
            "triggerprice": str(req.trigger_price if req.trigger_price is not None else 0),
            "ordertag": req.client_tag[:20],
        }
        res = angelone_orders.place_order(body)
        st = res.get("status")
        if st == "OK":
            self._breaker.record_success()
            data = res.get("data") or {}
            return OrderAck(ok=True, client_tag=req.client_tag,
                            broker_order_id=str(data.get("orderid") or ""),
                            unique_order_id=str(data.get("uniqueorderid") or ""),
                            status=OStatus.ACCEPTED, raw=res.get("raw") or {})
        if st == "REJECTED":
            self._breaker.record_success()   # broker answered — not an infra failure
            return OrderAck(ok=False, client_tag=req.client_tag, status=OStatus.REJECTED,
                            error=res.get("error") or "rejected", raw=res.get("raw") or {})
        # ERROR / AUTH_FAILED / CONFIG_REQUIRED
        self._breaker.record_failure()
        ambiguous = bool(res.get("network"))
        return OrderAck(ok=False, client_tag=req.client_tag, status=OStatus.UNKNOWN,
                        ambiguous=ambiguous,
                        error=(res.get("error") or st or "submit failed"), raw=res.get("raw") or {})

    def market_entry(self, req: OrderReq) -> OrderAck:
        return self._submit(req, is_exit=False)

    def limit_entry(self, req: OrderReq) -> OrderAck:
        return self._submit(req, is_exit=False)

    def stoploss_market(self, req: OrderReq) -> OrderAck:
        return self._submit(req, is_exit=True)

    def stoploss_limit(self, req: OrderReq) -> OrderAck:
        return self._submit(req, is_exit=True)

    def target_exit(self, req: OrderReq) -> OrderAck:
        return self._submit(req, is_exit=True)

    # ---------------------------------------------------------------- lifecycle
    def modify_order(self, req: OrderReq, **changes) -> OrderAck:
        self._guard_live("order modify")
        blocked = self._rate()
        if blocked is not None:
            return blocked
        body = {"variety": req.variety or "NORMAL",
                "orderid": req.broker_order_id if hasattr(req, "broker_order_id") else "",
                "tradingsymbol": req.tradingsymbol or req.symbol,
                "symboltoken": str(req.symboltoken), "exchange": req.exchange,
                "ordertype": _ORDER_TYPE_MAP.get(req.order_type, "LIMIT"),
                "producttype": req.product or "INTRADAY", "duration": "DAY",
                "quantity": str(int(req.quantity))}
        for k, v in changes.items():
            if k == "limit_price" and v is not None:
                body["price"] = str(v)
            if k == "trigger_price" and v is not None:
                body["triggerprice"] = str(v)
            if k == "orderid":
                body["orderid"] = str(v)
        res = angelone_orders.modify_order(body)
        ok = res.get("status") == "OK"
        return OrderAck(ok=ok, client_tag=req.client_tag,
                        status=OStatus.OPEN if ok else OStatus.UNKNOWN,
                        ambiguous=bool(res.get("network")), error=res.get("error") or "",
                        raw=res.get("raw") or {})

    def cancel_order(self, client_tag: str, broker_order_id: str = "", variety: str = "NORMAL") -> OrderAck:
        self._guard_live("order cancel")
        blocked = self._rate()
        if blocked is not None:
            return blocked
        res = angelone_orders.cancel_order(broker_order_id, variety)
        ok = res.get("status") == "OK"
        return OrderAck(ok=ok, client_tag=client_tag,
                        status=OStatus.CANCELLED if ok else OStatus.UNKNOWN,
                        ambiguous=bool(res.get("network")), error=res.get("error") or "",
                        raw=res.get("raw") or {})

    # ---------------------------------------------------------------- reads
    def get_order_status(self, broker_order_id: str = "", unique_order_id: str = "",
                         client_tag: str = "") -> OrderStatusResult:
        def _do():
            if unique_order_id:
                r = angelone_orders.order_details(unique_order_id)
                if r.get("status") == "OK":
                    return r
            return angelone_orders.find_in_book(orderid=broker_order_id,
                                                unique_order_id=unique_order_id,
                                                ordertag=client_tag)
        try:
            res = call_with_retry(_do, retries=self._read_retries)
        except Exception as e:                       # noqa: BLE001
            return OrderStatusResult(status=OStatus.UNKNOWN, text=f"{type(e).__name__}: {e}")
        if res.get("status") != "OK":
            return OrderStatusResult(status=OStatus.UNKNOWN, text=res.get("status") or "lookup failed")
        d = res.get("data") or {}
        req_q = d.get("quantity")
        fill_q = d.get("filledshares") or d.get("filledquantity") or 0
        status = map_broker_status(d.get("orderstatus") or d.get("status"), req_q, fill_q)
        return OrderStatusResult(
            status=status, filled_qty=float(fill_q or 0),
            pending_qty=float(d.get("unfilledshares") or 0) or None,
            avg_price=float(d.get("averageprice") or 0) or None,
            broker_order_id=str(d.get("orderid") or broker_order_id),
            unique_order_id=str(d.get("uniqueorderid") or unique_order_id),
            text=str(d.get("text") or d.get("orderstatus") or ""), raw=d)

    def get_order_book(self) -> list:
        res = call_with_retry(angelone_orders.order_book, retries=self._read_retries)
        return res.get("data") or [] if isinstance(res, dict) else []

    def get_positions(self) -> PositionSnapshot:
        res = call_with_retry(angelone.fetch_positions, retries=self._read_retries)
        if not isinstance(res, dict) or res.get("status") != "OK":
            return PositionSnapshot(ok=False, error=str((res or {}).get("status") or "positions failed"))
        rows = []
        for p in res.get("positions", []):
            rows.append(BrokerPosition(
                symbol=p.get("symbol") or "", symboltoken=str(p.get("symboltoken") or ""),
                exchange=p.get("exchange") or "", net_qty=float(p.get("net_qty") or 0),
                avg_price=p.get("avg_price"), ltp=p.get("ltp"),
                option_type=p.get("option_type") or "", strike=p.get("strike"),
                product=p.get("product") or ""))
        return PositionSnapshot(ok=True, positions=rows)
