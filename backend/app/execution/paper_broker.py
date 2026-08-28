"""
PaperBroker — a fully local BrokerBase.

No network. Fills are modelled from the market-feed LTP (or an explicit
expected price) so the SAME OrderManager / TradeMonitor / Reconciler code runs
in PAPER mode exactly as it would in LIVE.

Deterministic and scriptable for tests via `scenario`:

    fill_mode : "FULL" | "PARTIAL" | "REJECT" | "TIMEOUT" | "WORKING" | "AMBIGUOUS"
    partial_ratio : fraction filled when fill_mode == "PARTIAL"   (default 0.33)
    slippage : points added (BUY) / subtracted (SELL) to the fill  (default 0)
    position_override : net qty reconcile_position should report (for mismatch tests)
"""
from __future__ import annotations

import time
import random
from datetime import datetime, timezone

from .broker_base import (
    BrokerBase, OrderReq, OrderAck, OrderStatusResult, PositionSnapshot,
    BrokerPosition, Side, OrderType, OStatus, map_broker_status,
)


def _now():
    return datetime.now(timezone.utc).isoformat()


class BrokerTimeout(Exception):
    pass


class PaperBroker(BrokerBase):
    name = "paper"
    mode = "PAPER"

    def __init__(self, ltp_provider=None, scenario: dict | None = None):
        # ltp_provider(symboltoken) -> float | None
        self._ltp = ltp_provider or (lambda tok: None)
        self.scenario = dict(scenario or {})
        self._orders: dict[str, dict] = {}      # broker_order_id -> record
        self._by_tag: dict[str, str] = {}       # client_tag -> broker_order_id
        self._seq = 0

    # -- session (no-ops) ---------------------------------------------------
    def login(self):
        return {"status": "OK", "mode": "PAPER"}

    def refresh_session(self):
        return {"status": "OK", "mode": "PAPER"}

    def logout(self):
        return {"status": "OK"}

    # -- helpers ---------------------------------------------------------
    def _oid(self):
        self._seq += 1
        return f"PAPER-{self._seq:06d}"

    def _ref_price(self, req: OrderReq) -> float:
        px = self._ltp(req.symboltoken)
        if px is None:
            px = req.limit_price or req.trigger_price or 0.0
        slip = float(self.scenario.get("slippage") or 0.0)
        return round(px + (slip if req.side == Side.BUY else -slip), 2)

    def _make(self, req: OrderReq, *, working: bool) -> OrderAck:
        mode = self.scenario.get("fill_mode", "FULL")
        if mode == "TIMEOUT":
            raise BrokerTimeout("paper: simulated submit timeout")
        oid = self._oid()
        ref = self._ref_price(req)
        rec = {
            "broker_order_id": oid, "unique_order_id": oid + "U",
            "client_tag": req.client_tag, "leg": req.leg, "side": req.side,
            "order_type": req.order_type, "requested_qty": float(req.quantity),
            "limit_price": req.limit_price, "trigger_price": req.trigger_price,
            "symboltoken": req.symboltoken, "symbol": req.symbol, "exchange": req.exchange,
            "filled_qty": 0.0, "avg_price": None, "status_text": "open",
            "created": time.monotonic(),
        }
        if mode == "REJECT":
            rec["status_text"] = "rejected"
            rec["reject_reason"] = self.scenario.get("reject_reason", "RMS: margin shortfall")
        elif mode == "WORKING" or working:
            rec["status_text"] = "open"        # unfilled until _try_fill sees a cross
        elif mode == "PARTIAL":
            ratio = float(self.scenario.get("partial_ratio", 0.33))
            rec["filled_qty"] = round(float(req.quantity) * ratio)
            rec["avg_price"] = ref
            rec["status_text"] = "open"        # rest still working
        else:  # FULL
            rec["filled_qty"] = float(req.quantity)
            rec["avg_price"] = ref
            rec["status_text"] = "complete"

        self._orders[oid] = rec
        self._by_tag[req.client_tag] = oid

        if mode == "AMBIGUOUS":
            # the order DID land here, but the caller is told the call failed
            return OrderAck(ok=False, client_tag=req.client_tag, ambiguous=True,
                            status=OStatus.UNKNOWN, error="paper: simulated ambiguous network error")

        if mode == "REJECT":
            return OrderAck(ok=False, client_tag=req.client_tag, broker_order_id=oid,
                            unique_order_id=oid + "U", status=OStatus.REJECTED,
                            error=rec.get("reject_reason") or "rejected", raw={"paper": True})

        return OrderAck(ok=True, client_tag=req.client_tag, broker_order_id=oid,
                        unique_order_id=oid + "U",
                        status=map_broker_status(rec["status_text"], rec["requested_qty"],
                                                 rec["filled_qty"]),
                        raw={"paper": True})

    def _try_fill(self, rec: dict):
        """Fill a WORKING limit/SL order once LTP crosses its price."""
        if rec["status_text"] not in ("open",) or rec["filled_qty"] >= rec["requested_qty"]:
            return
        px = self._ltp(rec["symboltoken"])
        if px is None:
            return
        want = None
        if rec["order_type"] == OrderType.LIMIT and rec["limit_price"]:
            if (rec["side"] == Side.BUY and px <= rec["limit_price"]) or \
               (rec["side"] == Side.SELL and px >= rec["limit_price"]):
                want = rec["limit_price"]
        elif rec["order_type"] in (OrderType.SL, OrderType.SL_M) and rec["trigger_price"]:
            if (rec["side"] == Side.BUY and px >= rec["trigger_price"]) or \
               (rec["side"] == Side.SELL and px <= rec["trigger_price"]):
                want = px if rec["order_type"] == OrderType.SL_M else rec["limit_price"] or px
        if want is not None:
            rec["filled_qty"] = rec["requested_qty"]
            rec["avg_price"] = round(want, 2)
            rec["status_text"] = "complete"

    # -- entries ------------------------------------------------------------
    def market_entry(self, req: OrderReq) -> OrderAck:
        return self._make(req, working=False)

    def limit_entry(self, req: OrderReq) -> OrderAck:
        return self._make(req, working=True)

    def stoploss_market(self, req: OrderReq) -> OrderAck:
        return self._make(req, working=True)

    def stoploss_limit(self, req: OrderReq) -> OrderAck:
        return self._make(req, working=True)

    def target_exit(self, req: OrderReq) -> OrderAck:
        # an explicit exit is treated as immediate at LTP unless scripted otherwise
        return self._make(req, working=False)

    # -- lifecycle --------------------------------------------------------
    def modify_order(self, req: OrderReq, **changes) -> OrderAck:
        oid = self._by_tag.get(req.client_tag)
        if not oid:
            return OrderAck(ok=False, client_tag=req.client_tag, error="unknown order")
        rec = self._orders[oid]
        for k in ("limit_price", "trigger_price"):
            if k in changes and changes[k] is not None:
                rec[k] = changes[k]
        return OrderAck(ok=True, client_tag=req.client_tag, broker_order_id=oid,
                        unique_order_id=oid + "U", status=OStatus.OPEN)

    def cancel_order(self, client_tag: str, broker_order_id: str = "", variety="NORMAL") -> OrderAck:
        oid = broker_order_id or self._by_tag.get(client_tag)
        rec = self._orders.get(oid)
        if not rec:
            return OrderAck(ok=False, client_tag=client_tag, error="unknown order")
        if rec["status_text"] not in ("complete", "rejected"):
            rec["status_text"] = "cancelled"
        return OrderAck(ok=True, client_tag=client_tag, broker_order_id=oid,
                        status=OStatus.CANCELLED)

    # -- reads ----------------------------------------------------------
    def get_order_status(self, broker_order_id: str = "", unique_order_id: str = "",
                         client_tag: str = "") -> OrderStatusResult:
        rec = self._orders.get(broker_order_id)
        if not rec and unique_order_id:
            rec = next((r for r in self._orders.values() if r["unique_order_id"] == unique_order_id), None)
        if not rec and client_tag:
            rec = self._orders.get(self._by_tag.get(client_tag, ""))
        if not rec:
            return OrderStatusResult(status=OStatus.UNKNOWN, text="not found")
        self._try_fill(rec)
        status = map_broker_status(rec["status_text"], rec["requested_qty"], rec["filled_qty"])
        return OrderStatusResult(
            status=status, filled_qty=rec["filled_qty"],
            pending_qty=max(0.0, rec["requested_qty"] - rec["filled_qty"]),
            avg_price=rec["avg_price"], broker_order_id=rec["broker_order_id"],
            unique_order_id=rec["unique_order_id"],
            text=rec.get("reject_reason") or rec["status_text"], raw=dict(rec))

    def get_order_book(self) -> list:
        for rec in self._orders.values():
            self._try_fill(rec)
        return [dict(r) for r in self._orders.values()]

    def get_positions(self) -> PositionSnapshot:
        if "position_override" in self.scenario:
            ov = self.scenario["position_override"]
            rows = []
            for tok, q in (ov.items() if isinstance(ov, dict) else []):
                rows.append(BrokerPosition(symbol="", symboltoken=str(tok), exchange="",
                                           net_qty=float(q), avg_price=None))
            return PositionSnapshot(ok=True, positions=rows)
        agg: dict[str, float] = {}
        avg: dict[str, float] = {}
        for rec in self._orders.values():
            self._try_fill(rec)
            if rec["filled_qty"] <= 0:
                continue
            tok = str(rec["symboltoken"])
            # side already encodes direction: an exit is submitted as the
            # opposite side, so a signed sum of fills is the net position.
            sgn = 1.0 if rec["side"] == Side.BUY else -1.0
            agg[tok] = agg.get(tok, 0.0) + sgn * rec["filled_qty"]
            if rec["avg_price"]:
                avg[tok] = rec["avg_price"]
        rows = [BrokerPosition(symbol="", symboltoken=t, exchange="", net_qty=q,
                               avg_price=avg.get(t)) for t, q in agg.items() if abs(q) > 1e-9]
        return PositionSnapshot(ok=True, positions=rows)
