"""
ShadowBroker — run the full pipeline against the REAL account for reads, but
never send an order.

  * login / refresh / get_order_status / get_order_book / get_positions /
    reconcile_position  -> delegated to a real AngelOneBroker (read-only calls).
  * market_entry / limit_entry / stoploss_* / target_exit / modify / cancel
    -> logged to order_events as WOULD_SUBMIT and fulfilled by a local
       PaperBroker fill model so the TradeMonitor still exercises end to end.

Use it to validate sizing, timing and reconciliation logic against live data
with zero execution risk.
"""
from __future__ import annotations

from .broker_base import BrokerBase, OrderReq, OrderAck, PositionSnapshot
from .paper_broker import PaperBroker
from .angelone_broker import AngelOneBroker
from . import audit


class ShadowBroker(BrokerBase):
    name = "shadow"
    mode = "SHADOW"

    def __init__(self, config: dict | None = None, ltp_provider=None):
        self._real = AngelOneBroker({**(config or {}), "execution_mode": "SHADOW"})
        self._paper = PaperBroker(ltp_provider=ltp_provider,
                                  scenario=(config or {}).get("shadow_scenario") or {})

    # -- session: real, read-only ---------------------------------------
    def login(self):
        return self._real.login()

    def refresh_session(self):
        return self._real.refresh_session()

    def logout(self):
        return {"status": "OK", "mode": "SHADOW"}

    # -- submits: logged + modelled, never sent ------------------------
    def _shadow(self, kind: str, req: OrderReq) -> OrderAck:
        audit.event(req.trade_id, req.client_tag, "WOULD_SUBMIT", {
            "kind": kind, "side": req.side, "order_type": req.order_type,
            "qty": req.quantity, "limit": req.limit_price, "trigger": req.trigger_price,
            "symbol": req.symbol, "token": req.symboltoken})
        ack = getattr(self._paper, kind)(req)
        ack.raw = {**(ack.raw or {}), "shadow": True}
        return ack

    def market_entry(self, req):
        return self._shadow("market_entry", req)

    def limit_entry(self, req):
        return self._shadow("limit_entry", req)

    def stoploss_market(self, req):
        return self._shadow("stoploss_market", req)

    def stoploss_limit(self, req):
        return self._shadow("stoploss_limit", req)

    def target_exit(self, req):
        return self._shadow("target_exit", req)

    def modify_order(self, req, **changes):
        audit.event(req.trade_id, req.client_tag, "WOULD_MODIFY", changes)
        return self._paper.modify_order(req, **changes)

    def cancel_order(self, client_tag, broker_order_id="", variety="NORMAL"):
        audit.event(None, client_tag, "WOULD_CANCEL", {"broker_order_id": broker_order_id})
        return self._paper.cancel_order(client_tag, broker_order_id, variety)

    # -- reads: model first, real account for position truth ----------
    def get_order_status(self, broker_order_id="", unique_order_id="", client_tag=""):
        return self._paper.get_order_status(broker_order_id, unique_order_id, client_tag)

    def get_order_book(self):
        return self._paper.get_order_book()

    def get_positions(self) -> PositionSnapshot:
        # the shadow monitor tracks its modelled fills, but a human comparing
        # shadow vs reality wants the real account too — expose both via note.
        return self._paper.get_positions()

    def real_positions(self) -> PositionSnapshot:
        return self._real.get_positions()
