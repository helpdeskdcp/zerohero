"""
Immediate local Trade Monitor.

Created the instant an entry order is SUBMITTED — it does not wait for REST
confirmation or a delayed WebSocket position update. It marks the position to
the freshest validated market-feed LTP and decides target / SL / trailing /
time exits.

It is a PROVISIONAL execution-state monitor. It is NOT proof the broker filled
anything. The Reconciler feeds it real fills (`on_fill`) and real rejections
(`on_reject` / `on_cancel`); until a fill is confirmed, P&L and the entry
anchor are provisional and every decision is tagged `provisional=True`.

The monitor never places an order. `step()` returns an ExitDecision describing
what SHOULD happen; the OrderManager decides whether that means "alert only"
(default, monitor-only) or "submit a broker exit" (LIVE + auto_exit).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .broker_base import Side, OStatus
from .trade_state import TradeState


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _now():
    return datetime.now(timezone.utc)


@dataclass
class ExitDecision:
    reason: str                 # TARGET | STOP | TRAIL | TIME | REJECTED | CANCELLED
    price: float
    quantity: float
    provisional: bool
    note: str = ""


@dataclass
class MonitorState:
    trade_id: str
    side: str
    state: str = "PROVISIONAL"          # PROVISIONAL | CONFIRMED | PARTIAL | CLOSED
    entry_ref: float = 0.0
    quantity: float = 0.0
    target: Optional[float] = None
    target_2: Optional[float] = None
    stop: Optional[float] = None
    trail_distance: Optional[float] = None
    trail_trigger: Optional[float] = None
    breakeven_trigger: Optional[float] = None
    profit_lock: Optional[float] = None
    opened_ts: str = field(default_factory=lambda: _now().isoformat())
    max_hold_sec: Optional[float] = None
    last_ltp: Optional[float] = None
    last_ltp_ts: Optional[str] = None
    mfe: float = 0.0                    # max favourable excursion, points
    mae: float = 0.0                    # max adverse excursion, points
    live_pnl: Optional[float] = None
    trail_armed: bool = False
    exit_reason: Optional[str] = None
    exit_price: Optional[float] = None
    closed_ts: Optional[str] = None


class TradeMonitor:
    def __init__(self, state: TradeState, *, provisional_ltp: float | None = None,
                 now_fn=_now):
        self._now = now_fn
        s = state
        self.trade_id = s.trade_id
        self.side = s.side
        self.st = MonitorState(
            trade_id=s.trade_id, side=s.side,
            entry_ref=s.entry_ref or _f(provisional_ltp) or 0.0,
            quantity=s.monitor_qty,
            target=(s.target.price if s.target else None),
            target_2=(s.target.price_2 if s.target else None),
            stop=(s.stop.price if s.stop else None),
            trail_distance=(s.stop.trail_trigger if s.stop else None),
            trail_trigger=(s.stop.trail_trigger if s.stop else None),
            breakeven_trigger=(s.stop.breakeven_trigger if s.stop else None),
            profit_lock=(s.stop.profit_lock if s.stop else None),
            max_hold_sec=s.max_hold_sec,
            opened_ts=self._now().isoformat(),
        )
        self._closed = False

    # ------------------------------------------------------------- fills
    def on_fill(self, filled_qty: float, avg_price: Optional[float], *, complete: bool):
        fq = _f(filled_qty)
        ap = _f(avg_price)
        if ap and ap > 0:
            # re-anchor everything the fill moved (distances preserved by TradeState.apply_fill,
            # but the monitor keeps its own copy so it works standalone in tests)
            if self.st.target is not None and self.st.entry_ref:
                d = self.st.target - self.st.entry_ref
                self.st.target = round(ap + d, 2)
            if self.st.stop is not None and self.st.entry_ref and not self._stop_trailed():
                d = self.st.stop - self.st.entry_ref
                self.st.stop = round(ap + d, 2)
            self.st.entry_ref = round(ap, 2)
        if fq is not None and fq >= 0:
            self.st.quantity = fq
        self.st.state = "CONFIRMED" if complete else ("PARTIAL" if (fq or 0) > 0 else self.st.state)

    def on_reject(self, note: str = "") -> ExitDecision:
        self._close("REJECTED", self.st.entry_ref, note)
        return ExitDecision("REJECTED", self.st.entry_ref, 0.0, provisional=False, note=note)

    def on_cancel(self, note: str = "") -> ExitDecision:
        self._close("CANCELLED", self.st.entry_ref, note)
        return ExitDecision("CANCELLED", self.st.entry_ref, 0.0, provisional=False, note=note)

    # ------------------------------------------------------------- tick
    def step(self, ltp: float, now: Optional[datetime] = None) -> Optional[ExitDecision]:
        """Mark to `ltp`; update excursions; ratchet the trail (never loosen);
        return an ExitDecision if a level is breached."""
        if self._closed:
            return None
        px = _f(ltp)
        now = now or self._now()
        if px is None or px <= 0:
            return None

        s = self.st
        sgn = 1.0 if self.side == Side.BUY else -1.0
        s.last_ltp = px
        s.last_ltp_ts = now.isoformat()
        fav = sgn * (px - s.entry_ref)
        s.mfe = round(max(s.mfe, fav if fav > 0 else 0.0), 4)
        s.mae = round(max(s.mae, -fav if fav < 0 else 0.0), 4)
        if s.quantity:
            s.live_pnl = round(fav * s.quantity, 2)

        provisional = s.state in ("PROVISIONAL", "PARTIAL")
        qty = s.quantity or 0.0

        # --- time stop (scalp discipline) — even while provisional ---
        if s.max_hold_sec:
            try:
                opened = datetime.fromisoformat(s.opened_ts.replace("Z", "+00:00"))
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=timezone.utc)
                if (now - opened).total_seconds() >= float(s.max_hold_sec):
                    return self._fire("TIME", px, qty, provisional, "max hold elapsed")
            except Exception:
                pass

        # --- breakeven move: once 1R favourable, stop can't be worse than entry ---
        if s.breakeven_trigger and s.mfe >= float(s.breakeven_trigger):
            be = s.entry_ref
            if s.stop is None or (self.side == Side.BUY and be > s.stop) or \
               (self.side == Side.SELL and be < s.stop):
                s.stop = round(be, 2)

        # --- trailing ratchet: arm at trail_trigger, then lock (mfe - profit_lock) ---
        if s.trail_trigger and s.mfe >= float(s.trail_trigger):
            s.trail_armed = True
        if s.trail_armed and s.profit_lock is not None:
            locked_excursion = s.mfe - float(s.profit_lock)
            if locked_excursion > 0:
                cand = round(s.entry_ref + sgn * locked_excursion, 2)
                if s.stop is None or (self.side == Side.BUY and cand > s.stop) or \
                   (self.side == Side.SELL and cand < s.stop):
                    s.stop = cand           # never loosens: cand only replaces a worse stop

        # --- exits ---
        if s.target is not None and (
                (self.side == Side.BUY and px >= s.target) or
                (self.side == Side.SELL and px <= s.target)):
            return self._fire("TARGET", px, qty, provisional, "target touched")

        if s.stop is not None and (
                (self.side == Side.BUY and px <= s.stop) or
                (self.side == Side.SELL and px >= s.stop)):
            reason = "TRAIL" if s.trail_armed or (
                (self.side == Side.BUY and s.stop >= s.entry_ref) or
                (self.side == Side.SELL and s.stop <= s.entry_ref)) else "STOP"
            return self._fire(reason, px, qty, provisional, f"{reason.lower()} touched @ {s.stop}")

        return None

    # ------------------------------------------------------------- helpers
    def _stop_trailed(self) -> bool:
        s = self.st
        if s.stop is None or not s.entry_ref:
            return False
        return (self.side == Side.BUY and s.stop >= s.entry_ref) or \
               (self.side == Side.SELL and s.stop <= s.entry_ref)

    def _fire(self, reason, price, qty, provisional, note) -> ExitDecision:
        self._close(reason, price, note)
        return ExitDecision(reason, round(price, 2), qty, provisional=provisional, note=note)

    def _close(self, reason, price, note):
        self._closed = True
        self.st.state = "CLOSED"
        self.st.exit_reason = reason
        self.st.exit_price = round(_f(price) or 0.0, 2)
        self.st.closed_ts = self._now().isoformat()

    @property
    def closed(self) -> bool:
        return self._closed

    def snapshot(self) -> dict:
        return dict(self.st.__dict__)
