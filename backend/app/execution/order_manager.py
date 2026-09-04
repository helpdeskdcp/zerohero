"""
OrderManager — the single entry point the pipelines use.

    contract  ->  prearm()  ->  TradeState (target/SL/monitor params pre-computed)
              ->  submit()   ->  broker entry  +  TradeMonitor created IMMEDIATELY
                                 (does not wait for confirmation)
              ->  on_ltp()   ->  monitor decides TARGET/SL/TRAIL/TIME
              ->  reconcile() ->  broker order status + position = source of truth
              ->  recover()   ->  on restart, reconcile open intents, never re-send

Safety invariants enforced here:
  * "API returned ok" is NOT a fill — the monitor stays provisional until the
    reconciler confirms an average fill price.
  * idempotent per leg (trade_id:LEG) — a duplicate submit is suppressed.
  * an ambiguous submit is NEVER retried; it is reconciled.
  * kill switch / staleness / freeze block NEW entries; existing monitors keep
    running.
  * PARTIAL fills size the exits from the ACTUAL filled quantity.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .broker_base import BrokerBase, LiveDisabled, OrderType, Leg, Side, OStatus

_log = logging.getLogger(__name__)
from .trade_state import TradeState
from .trade_monitor import TradeMonitor, ExitDecision
from .reconciler import Reconciler
from . import idempotency as idem
from . import audit
from . import killswitch
from .staleness import Clocks, assess


def _now():
    return datetime.now(timezone.utc)


def _now_iso():
    return _now().isoformat()


# ---------------------------------------------------------------- broker factory
def make_broker(mode: str, config: dict | None = None, *, ltp_provider=None) -> BrokerBase:
    mode = (mode or "PAPER").upper()
    cfg = dict(config or {})
    cfg["execution_mode"] = mode
    if mode == "LIVE":
        from .angelone_broker import AngelOneBroker
        return AngelOneBroker(cfg)
    if mode == "SHADOW":
        from .shadow_broker import ShadowBroker
        return ShadowBroker(cfg, ltp_provider=ltp_provider)
    from .paper_broker import PaperBroker
    return PaperBroker(ltp_provider=ltp_provider, scenario=cfg.get("paper_scenario") or {})


@dataclass
class SubmitResult:
    status: str                       # SUBMITTED|DUPLICATE_SUPPRESSED|AMBIGUOUS|REJECTED|
                                      # BLOCKED_KILLSWITCH|BLOCKED_RISK|BLOCKED_STALE|
                                      # BLOCKED_FROZEN|LIVE_DISABLED
    state: Optional[TradeState] = None
    monitor: Optional[TradeMonitor] = None
    ack: object = None
    reasons: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "SUBMITTED"


class OrderManager:
    def __init__(self, *, mode: str = "PAPER", broker: BrokerBase | None = None,
                 config: dict | None = None, ltp_provider=None, now_fn=_now,
                 on_alert=None):
        self.config = dict(config or {})
        self.mode = (mode or self.config.get("execution_mode") or "PAPER").upper()
        self._ltp = ltp_provider or (lambda tok: None)
        self.broker = broker or make_broker(self.mode, self.config, ltp_provider=self._ltp)
        self.reconciler = Reconciler(self.broker, now_fn=_now_iso)
        self._now = now_fn
        self._on_alert = on_alert          # callable(kind:str, payload:dict) — off critical path
        self.frozen = False
        self.freeze_reason = ""
        # daily risk halt — set by the runner from realised P&L vs max_daily_loss.
        # Distinct from `frozen` (reconciler FREEZE) and the kill switch; it
        # clears on its own when P&L recovers / the day rolls.
        self.risk_halt = False
        self.risk_halt_reason = ""
        # live registries (also persisted via broker_orders / order_events)
        self.states: dict[str, TradeState] = {}
        self.monitors: dict[str, TradeMonitor] = {}

    # ---------------------------------------------------------------- freeze
    def freeze(self, reason: str):
        if not self.frozen:
            self.frozen = True
            self.freeze_reason = reason
            audit.event(None, None, "FREEZE", {"reason": reason})

    def unfreeze(self, reason: str = "manual"):
        if self.frozen:
            self.frozen = False
            self.freeze_reason = ""
            audit.event(None, None, "UNFREEZE", {"reason": reason})

    def set_risk_halt(self, active: bool, reason: str = ""):
        """Daily risk / max-loss guard. Blocks NEW entries; running monitors are
        untouched. Idempotent — only logs on a transition."""
        active = bool(active)
        if active == self.risk_halt:
            self.risk_halt_reason = reason if active else ""
            return
        self.risk_halt = active
        self.risk_halt_reason = reason if active else ""
        audit.event(None, None, "RISK_HALT_ON" if active else "RISK_HALT_OFF", {"reason": reason})

    def _entries_blocked(self, state: TradeState | None) -> Optional[SubmitResult]:
        if killswitch.is_active():
            return SubmitResult("BLOCKED_KILLSWITCH", state,
                                reasons=[f"kill switch active: {killswitch.state().get('reason')}"])
        if self.risk_halt:
            return SubmitResult("BLOCKED_RISK", state,
                                reasons=[f"daily risk halt: {self.risk_halt_reason}"])
        if self.frozen:
            return SubmitResult("BLOCKED_FROZEN", state,
                                reasons=[f"order manager frozen: {self.freeze_reason}"])
        return None

    # ---------------------------------------------------------------- pre-arm
    def prearm(self, contract: dict, *, instrument: dict | None = None,
               quantity: float | None = None) -> TradeState:
        """Build + validate the full local TradeState and persist a PREARMED
        ENTRY intent. Raises PrearmRejected on an unusable contract."""
        state = TradeState.from_contract(contract, instrument=instrument, quantity=quantity,
                                         default_stop_pct=self.config.get("default_stop_pct", 0.35),
                                         default_target_rr=self.config.get("default_target_rr", 1.6))
        req = state.order_req(
            Leg.ENTRY,
            order_type=state.entry_type,
            quantity=state.quantity,
            limit_price=state.limit_price if state.entry_type == OrderType.LIMIT else None)
        idem.prearm(req, self.mode)
        self.states[state.trade_id] = state
        audit.event(state.trade_id, req.client_tag, "PREARM", {
            "side": state.side, "qty": state.quantity, "entry_type": state.entry_type,
            "expected_entry": state.expected_entry_price,
            "target": state.target.price if state.target else None,
            "stop": state.stop.price if state.stop else None,
            "confidence": state.signal_confidence, "signal_ts": state.signal_ts,
            "market_data_ts": state.market_data_ts, "mode": self.mode})
        return state

    # ---------------------------------------------------------------- submit
    def submit(self, state: TradeState, *, clocks: Clocks | None = None) -> SubmitResult:
        blocked = self._entries_blocked(state)
        if blocked is not None:
            audit.event(state.trade_id, idem.tag(state.trade_id, Leg.ENTRY),
                        "SUBMIT_BLOCKED", {"status": blocked.status})
            return blocked

        # staleness — never open a NEW position on stale data
        if clocks is not None:
            rep = assess(clocks,
                         max_ltp_age=self.config.get("exec_stale_ltp_sec", 20),
                         max_reconcile_age=self.config.get("exec_stale_reconcile_sec", 90))
            if not rep.allow_new_entries:
                audit.event(state.trade_id, idem.tag(state.trade_id, Leg.ENTRY),
                            "SUBMIT_BLOCKED_STALE", {"reasons": rep.reasons, "ages": rep.ages})
                return SubmitResult("BLOCKED_STALE", state, reasons=rep.reasons)

        entry_tag = idem.tag(state.trade_id, Leg.ENTRY)

        # idempotency — a leg that is already live is never re-sent
        if idem.is_live(entry_tag):
            row = idem.get(entry_tag) or {}
            mon = self.monitors.get(state.trade_id)
            audit.event(state.trade_id, entry_tag, "SUBMIT_DUPLICATE_SUPPRESSED",
                        {"existing_status": row.get("status")})
            return SubmitResult("DUPLICATE_SUPPRESSED", state, mon, reasons=["entry already live"])
        if idem.is_terminal(entry_tag):
            return SubmitResult("DUPLICATE_SUPPRESSED", state, self.monitors.get(state.trade_id),
                                reasons=["entry already terminal"])

        # make sure a PREARMED row exists (submit may be called directly in tests)
        req = state.order_req(Leg.ENTRY, order_type=state.entry_type, quantity=state.quantity,
                              limit_price=state.limit_price if state.entry_type == OrderType.LIMIT else None)
        if not idem.get(entry_tag):
            idem.prearm(req, self.mode)
            self.states.setdefault(state.trade_id, state)

        # --- the one network call on the critical path ---
        try:
            if state.entry_type == OrderType.LIMIT:
                ack = self.broker.limit_entry(req)
            else:
                ack = self.broker.market_entry(req)
        except LiveDisabled as e:
            audit.event(state.trade_id, entry_tag, "LIVE_DISABLED", {"error": str(e)})
            return SubmitResult("LIVE_DISABLED", state, reasons=[str(e)])
        except Exception as e:                       # noqa: BLE001 — treat as ambiguous
            idem.mark_ambiguous(entry_tag, _AckLike(str(e)))
            audit.event(state.trade_id, entry_tag, "SUBMIT_EXCEPTION", {"error": str(e)[:200]})
            mon = self._start_monitor(state)         # still monitor — position may exist
            return SubmitResult("AMBIGUOUS", state, mon, reasons=[f"submit raised: {e}"])

        # --- create the local monitor IMMEDIATELY, regardless of ack ---
        monitor = self._start_monitor(state)

        if ack.ambiguous or (not ack.ok and ack.status == OStatus.UNKNOWN):
            idem.mark_ambiguous(entry_tag, ack)
            audit.event(state.trade_id, entry_tag, "SUBMIT_AMBIGUOUS", {"error": ack.error})
            return SubmitResult("AMBIGUOUS", state, monitor, ack,
                                reasons=[ack.error or "ambiguous — will reconcile"])

        if not ack.ok:
            idem.mark_status(entry_tag, OStatus.REJECTED, text=ack.error)
            dec = monitor.on_reject(ack.error) if monitor and not monitor.closed else None
            audit.event(state.trade_id, entry_tag, "SUBMIT_REJECTED", {"error": ack.error})
            self._alert("order_rejected", {"trade_id": state.trade_id, "error": ack.error})
            return SubmitResult("REJECTED", state, monitor, ack,
                                reasons=[ack.error or "rejected"])

        state.broker_order_id = ack.broker_order_id
        state.unique_order_id = ack.unique_order_id
        state.status = ack.status
        idem.mark_submitted(entry_tag, ack)
        audit.event(state.trade_id, entry_tag, "SUBMITTED", {
            "broker_order_id": ack.broker_order_id, "unique_order_id": ack.unique_order_id,
            "status": ack.status})
        return SubmitResult("SUBMITTED", state, monitor, ack)

    def _start_monitor(self, state: TradeState) -> TradeMonitor:
        prov = self._ltp(state.symboltoken) or state.expected_entry_price
        mon = TradeMonitor(state, provisional_ltp=prov, now_fn=self._now)
        self.monitors[state.trade_id] = mon
        return mon

    # ---------------------------------------------------------------- protective orders
    def place_protective_orders(self, state: TradeState) -> dict:
        """Optionally pre-place SL-M (and a target LIMIT) at the broker once the
        entry is confirmed. OFF by default (`place_broker_exits`) — the local
        monitor is authoritative and the app stays monitor-only unless a human
        turns this on for a LIVE session."""
        if not self.config.get("place_broker_exits"):
            return {"placed": [], "skipped": "place_broker_exits disabled"}
        if state.is_provisional:
            return {"placed": [], "skipped": "entry not confirmed"}
        placed = []
        exit_side = Side.opposite(state.side)
        if state.stop:
            sl_tag = idem.tag(state.trade_id, Leg.SL)
            if not idem.is_live(sl_tag) and not idem.is_terminal(sl_tag):
                req = state.order_req(Leg.SL, order_type=OrderType.SL_M, side=exit_side,
                                      quantity=state.monitor_qty, trigger_price=state.stop.price)
                idem.prearm(req, self.mode)
                ack = self.broker.stoploss_market(req)
                (idem.mark_submitted if ack.ok else idem.mark_ambiguous)(sl_tag, ack)
                placed.append(("SL", ack.ok, ack.error))
        if state.target and self.config.get("place_broker_target"):
            t_tag = idem.tag(state.trade_id, Leg.TARGET)
            if not idem.is_live(t_tag) and not idem.is_terminal(t_tag):
                req = state.order_req(Leg.TARGET, order_type=OrderType.LIMIT, side=exit_side,
                                      quantity=state.monitor_qty, limit_price=state.target.price)
                idem.prearm(req, self.mode)
                ack = self.broker.target_exit(req)
                (idem.mark_submitted if ack.ok else idem.mark_ambiguous)(t_tag, ack)
                placed.append(("TARGET", ack.ok, ack.error))
        audit.event(state.trade_id, idem.tag(state.trade_id, "PROTECTIVE"), "PROTECTIVE_ORDERS",
                    {"placed": placed})
        return {"placed": placed}

    # ---------------------------------------------------------------- monitor tick
    def on_ltp(self, trade_id: str, ltp: float) -> Optional[ExitDecision]:
        mon = self.monitors.get(trade_id)
        state = self.states.get(trade_id)
        if not mon or mon.closed or state is None:
            return None
        dec = mon.step(ltp, self._now())
        if dec is not None:
            self._handle_decision(state, mon, dec)
        return dec

    def _handle_decision(self, state: TradeState, mon: TradeMonitor, dec: ExitDecision):
        audit.event(state.trade_id, idem.tag(state.trade_id, Leg.EXIT), "MONITOR_DECISION", {
            "reason": dec.reason, "price": dec.price, "qty": dec.quantity,
            "provisional": dec.provisional, "note": dec.note})

        want_broker_exit = (
            self.mode == "LIVE" and self.config.get("auto_exit")
            and dec.reason in ("TARGET", "STOP", "TRAIL", "TIME")
            and not state.is_provisional and state.monitor_qty > 0
        )
        if want_broker_exit and not killswitch.is_active():
            self._submit_exit(state, dec)
        else:
            # monitor-only: alert, let the human square off
            self._alert("exit_signal", {
                "trade_id": state.trade_id, "reason": dec.reason, "price": dec.price,
                "qty": dec.quantity, "provisional": dec.provisional,
                "monitor": mon.snapshot(), "note": dec.note,
                "mode": self.mode, "auto_exit": bool(self.config.get("auto_exit"))})

    def _submit_exit(self, state: TradeState, dec: ExitDecision) -> SubmitResult:
        exit_tag = idem.tag(state.trade_id, Leg.EXIT)
        if idem.is_live(exit_tag) or idem.is_terminal(exit_tag):
            return SubmitResult("DUPLICATE_SUPPRESSED", state, self.monitors.get(state.trade_id),
                                reasons=["exit already sent"])
        req = state.order_req(Leg.EXIT, order_type=OrderType.MARKET,
                              side=Side.opposite(state.side), quantity=state.monitor_qty)
        idem.prearm(req, self.mode)
        try:
            ack = self.broker.target_exit(req)
        except LiveDisabled as e:
            audit.event(state.trade_id, exit_tag, "EXIT_LIVE_DISABLED", {"error": str(e)})
            self._alert("exit_signal", {"trade_id": state.trade_id, "reason": dec.reason,
                                        "price": dec.price, "note": "live disabled — alert only"})
            return SubmitResult("LIVE_DISABLED", state, reasons=[str(e)])
        except Exception as e:                       # noqa: BLE001
            idem.mark_ambiguous(exit_tag, _AckLike(str(e)))
            return SubmitResult("AMBIGUOUS", state, reasons=[str(e)])
        if ack.ok:
            idem.mark_submitted(exit_tag, ack)
            idem.mark_status(exit_tag, OStatus.ACCEPTED, exit_reason=dec.reason)
            audit.event(state.trade_id, exit_tag, "EXIT_SUBMITTED",
                        {"reason": dec.reason, "broker_order_id": ack.broker_order_id})
            return SubmitResult("SUBMITTED", state, self.monitors.get(state.trade_id), ack)
        (idem.mark_ambiguous if ack.ambiguous else
         lambda t, a: idem.mark_status(t, OStatus.REJECTED, text=a.error))(exit_tag, ack)
        return SubmitResult("AMBIGUOUS" if ack.ambiguous else "REJECTED", state, ack=ack,
                            reasons=[ack.error])

    # ---------------------------------------------------------------- reconcile
    def reconcile(self, trade_id: str) -> Optional[object]:
        state = self.states.get(trade_id)
        if state is None:
            return None
        mon = self.monitors.get(trade_id)
        res = self.reconciler.reconcile_leg(state, mon, leg=Leg.ENTRY)
        if res.action == "FREEZE":
            self.freeze("; ".join(res.reasons) or "reconciler FREEZE")
            self._alert("reconcile_freeze", {"trade_id": trade_id, "reasons": res.reasons,
                                             "order_status": res.order_status,
                                             "position_match": res.position_match})
        elif res.action == "DEAD":
            self._alert("order_dead", {"trade_id": trade_id, "status": res.order_status,
                                       "reasons": res.reasons})
        # also reconcile any EXIT leg that was sent
        exit_tag = idem.tag(trade_id, Leg.EXIT)
        if idem.get(exit_tag) and not idem.is_terminal(exit_tag):
            self.reconciler.reconcile_leg(state, mon, leg=Leg.EXIT)
        return res

    def reconcile_all(self) -> dict:
        return {tid: self.reconcile(tid) for tid in list(self.states.keys())}

    # ---------------------------------------------------------------- recovery
    def recover(self) -> dict:
        """On startup / leadership change: reconcile every non-terminal intent.
        NEVER re-submits — an ambiguous/accepted order is chased via status."""
        rows = idem.open_intents()
        by_trade: dict[str, list] = {}
        for r in rows:
            by_trade.setdefault(r["trade_id"], []).append(r)
        recovered = {}
        for tid, legs in by_trade.items():
            entry = next((l for l in legs if l["leg"] == Leg.ENTRY), legs[0])
            state = self._state_from_rows(tid, legs)
            self.states[tid] = state
            mon = self._start_monitor(state)
            res = self.reconciler.reconcile_leg(state, mon, leg=Leg.ENTRY)
            recovered[tid] = res.action
            audit.event(tid, entry["client_tag"], "RECOVERED",
                        {"action": res.action, "status": res.order_status})
            if res.action in ("DEAD",):
                self.monitors.pop(tid, None)
                self.states.pop(tid, None)      # abandoned intent — don't keep it "open"
        return recovered

    def _state_from_rows(self, trade_id: str, legs: list) -> TradeState:
        entry = next((l for l in legs if l["leg"] == Leg.ENTRY), legs[0])
        tgt = next((l for l in legs if l["leg"] == Leg.TARGET), None)
        sl = next((l for l in legs if l["leg"] == Leg.SL), None)
        from .trade_state import TargetPlan, StopPlan
        st = TradeState(
            trade_id=trade_id, strategy="RECOVERED",
            symbol=entry.get("symbol") or "", symboltoken=str(entry.get("symboltoken") or ""),
            exchange=entry.get("exchange") or "", tradingsymbol=entry.get("tradingsymbol") or "",
            product=entry.get("product") or "INTRADAY",
            side=entry.get("side") or Side.BUY,
            quantity=float(entry.get("requested_qty") or 0),
            entry_type=OrderType.LIMIT if entry.get("order_type") == OrderType.LIMIT else OrderType.MARKET,
            limit_price=entry.get("limit_price"),
            expected_entry_price=entry.get("limit_price") or entry.get("avg_fill_price"),
            broker_order_id=entry.get("broker_order_id") or "",
            unique_order_id=entry.get("unique_order_id") or "",
            status=entry.get("status") or OStatus.UNKNOWN,
            filled_qty=float(entry.get("filled_qty") or 0),
            avg_fill_price=entry.get("avg_fill_price"),
            signal_confidence=entry.get("signal_confidence"),
            signal_ts=entry.get("signal_ts"), market_data_ts=entry.get("market_data_ts"),
        )
        if tgt and (tgt.get("limit_price") or tgt.get("trigger_price")):
            px = tgt.get("limit_price") or tgt.get("trigger_price")
            st.target = TargetPlan(price=px, distance=abs(px - (st.entry_ref or px)))
        if sl and sl.get("trigger_price"):
            px = sl.get("trigger_price")
            st.stop = StopPlan(price=px, distance=abs((st.entry_ref or px) - px))
        return st

    # ---------------------------------------------------------------- misc
    def cancel(self, trade_id: str, leg: str = Leg.ENTRY) -> object:
        tag = idem.tag(trade_id, leg)
        row = idem.get(tag) or {}
        ack = self.broker.cancel_order(tag, row.get("broker_order_id") or "")
        if ack.ok:
            idem.mark_status(tag, OStatus.CANCELLED)
        audit.event(trade_id, tag, "CANCEL", {"ok": ack.ok, "error": ack.error})
        return ack

    def _alert(self, kind: str, payload: dict):
        if self._on_alert:
            try:
                self._on_alert(kind, payload)
            except Exception as e:
                _log.warning("OrderManager._alert(%s): alert callback raised: %r", kind, e)

    def status(self) -> dict:
        ks = killswitch.state()
        return {
            "mode": self.mode,
            "broker": self.broker.name,
            "frozen": self.frozen,
            "freeze_reason": self.freeze_reason,
            "risk_halt": self.risk_halt,
            "risk_halt_reason": self.risk_halt_reason,
            "kill_switch": ks,
            "live_enabled": getattr(self.broker, "live_enabled", False),
            "entries_allowed": not (ks.get("active") or self.frozen or self.risk_halt),
            "open_states": len(self.states),
            "open_monitors": sum(1 for m in self.monitors.values() if not m.closed),
            "config": {k: self.config.get(k) for k in (
                "auto_exit", "place_broker_exits", "exec_stale_ltp_sec",
                "exec_stale_reconcile_sec", "exec_reconcile_sec")},
        }


class _AckLike:
    """Minimal stand-in so idem.mark_ambiguous can take an exception string."""
    def __init__(self, error: str):
        self.error = error
        self.status = OStatus.UNKNOWN
