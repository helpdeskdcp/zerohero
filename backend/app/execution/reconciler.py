"""
Broker Reconciliation.

Continuously compares three views and lets the BROKER win on anything that
matters (filled quantity, actual position):

    LOCAL TradeState/Monitor   vs   BROKER ORDER STATUS   vs   BROKER POSITION

Outcomes it must handle: OPEN, COMPLETE, REJECTED, CANCELLED, PARTIAL, UNKNOWN,
and position mismatch. On UNKNOWN or a mismatch it returns FREEZE — the
OrderManager stops new entries and alerts; it NEVER re-sends the order (it may
already be live).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .broker_base import OStatus, Side, BrokerBase
from .trade_state import TradeState
from .trade_monitor import TradeMonitor
from . import idempotency as idem
from . import audit


def _now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ReconResult:
    action: str                      # OK | FILLED | PARTIAL | DEAD | FREEZE
    order_status: str = OStatus.UNKNOWN
    filled_qty: float = 0.0
    avg_price: Optional[float] = None
    position_match: str = "UNCHECKED"   # MATCH | MISMATCH | FLAT | UNCHECKED
    reasons: list = field(default_factory=list)
    exit_decision: object = None        # ExitDecision if the monitor was closed here


class Reconciler:
    def __init__(self, broker: BrokerBase, *, now_fn=_now):
        self.broker = broker
        self._now = now_fn

    # ---------------------------------------------------------------- one leg
    def reconcile_leg(self, state: TradeState, monitor: TradeMonitor | None,
                      *, leg: str = "ENTRY") -> ReconResult:
        tag = idem.tag(state.trade_id, leg)
        row = idem.get(tag) or {}
        boid = row.get("broker_order_id") or state.broker_order_id
        uoid = row.get("unique_order_id") or state.unique_order_id
        reasons = []

        # --- broker order status ---
        try:
            osr = self.broker.get_order_status(broker_order_id=boid, unique_order_id=uoid,
                                               client_tag=tag)
        except Exception as e:                       # noqa: BLE001
            reasons.append(f"order-status call failed: {type(e).__name__}: {str(e)[:60]}")
            return ReconResult("FREEZE", OStatus.UNKNOWN, reasons=reasons)

        st = osr.status
        req_qty = row.get("requested_qty") or state.quantity
        idem.mark_status(tag, st, filled_qty=osr.filled_qty, avg_price=osr.avg_price,
                         broker_order_id=osr.broker_order_id, unique_order_id=osr.unique_order_id,
                         text=osr.text)

        if st == OStatus.UNKNOWN:
            reasons.append("broker order status UNKNOWN — not acting, will retry")
            audit.event(state.trade_id, tag, "RECONCILE_UNKNOWN", {"raw": osr.text})
            return ReconResult("FREEZE", st, osr.filled_qty, osr.avg_price, reasons=reasons)

        if st in (OStatus.REJECTED, OStatus.CANCELLED):
            state.status = st
            dec = None
            if monitor and not monitor.closed:
                dec = monitor.on_reject(osr.text) if st == OStatus.REJECTED else monitor.on_cancel(osr.text)
            audit.event(state.trade_id, tag, f"RECONCILE_{st}", {"text": osr.text})
            return ReconResult("DEAD", st, osr.filled_qty or 0.0, osr.avg_price,
                               reasons=reasons + [f"broker {st}: {osr.text}"], exit_decision=dec)

        if st == OStatus.PARTIAL:
            state.apply_fill(osr.filled_qty, osr.avg_price, complete=False)
            if monitor:
                monitor.on_fill(osr.filled_qty, osr.avg_price, complete=False)
            reasons.append(f"PARTIAL fill {osr.filled_qty}/{req_qty} @ {osr.avg_price} — "
                           f"monitoring filled qty only")
            audit.event(state.trade_id, tag, "RECONCILE_PARTIAL",
                        {"filled": osr.filled_qty, "requested": req_qty, "avg": osr.avg_price})
            pm = self._check_position(state, osr.filled_qty, reasons)
            action = "FREEZE" if pm == "MISMATCH" else "PARTIAL"
            return ReconResult(action, st, osr.filled_qty or 0.0, osr.avg_price,
                               position_match=pm, reasons=reasons)

        if st == OStatus.COMPLETE:
            state.apply_fill(osr.filled_qty or req_qty, osr.avg_price, complete=True)
            if monitor:
                monitor.on_fill(osr.filled_qty or req_qty, osr.avg_price, complete=True)
            audit.event(state.trade_id, tag, "RECONCILE_COMPLETE",
                        {"filled": osr.filled_qty or req_qty, "avg": osr.avg_price})
            pm = self._check_position(state, osr.filled_qty or req_qty, reasons)
            action = "FREEZE" if pm == "MISMATCH" else "FILLED"
            return ReconResult(action, st, osr.filled_qty or req_qty, osr.avg_price,
                               position_match=pm, reasons=reasons)

        # OPEN / ACCEPTED — still working, nothing to do but note we looked
        audit.event(state.trade_id, tag, "RECONCILE_OPEN", {"status": st})
        return ReconResult("OK", st, osr.filled_qty or 0.0, osr.avg_price, reasons=reasons)

    # ---------------------------------------------------------------- position
    def _check_position(self, state: TradeState, expected_filled: float,
                        reasons: list) -> str:
        if not state.symboltoken:
            return "UNCHECKED"
        try:
            snap = self.broker.get_positions()
        except Exception as e:                       # noqa: BLE001
            reasons.append(f"position check failed: {type(e).__name__}")
            return "UNCHECKED"
        if not getattr(snap, "ok", False):
            # can't read positions -> we simply don't know; never a false mismatch
            reasons.append(f"positions unavailable ({getattr(snap, 'error', '')}) — not verified")
            return "UNCHECKED"
        pos = snap.by_token(state.symboltoken)
        sgn = 1.0 if state.side == Side.BUY else -1.0
        want = sgn * (expected_filled or 0.0)
        if pos is None:
            # no position for our token — only a mismatch if we think we're filled
            if abs(want) > 0:
                reasons.append(f"broker shows FLAT for {state.symboltoken} but local expects {want}")
                return "MISMATCH"
            return "FLAT"
        have = pos.net_qty or 0.0
        # tolerate the position carrying OTHER lots (combos): mismatch only if the
        # broker has LESS exposure on our side than we booked.
        if want > 0 and have < want - 1e-6:
            reasons.append(f"position mismatch: broker net {have} < expected {want}")
            return "MISMATCH"
        if want < 0 and have > want + 1e-6:
            reasons.append(f"position mismatch: broker net {have} > expected {want}")
            return "MISMATCH"
        return "MATCH"

    # ---------------------------------------------------------------- bulk
    def resolve_all(self, states: dict, monitors: dict) -> dict:
        """states: {trade_id: TradeState}, monitors: {trade_id: TradeMonitor}.
        Returns {trade_id: ReconResult}."""
        out = {}
        for tid, state in states.items():
            out[tid] = self.reconcile_leg(state, monitors.get(tid))
        return out
