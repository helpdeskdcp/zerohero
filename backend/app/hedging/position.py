"""
Paper position tracking + exit rules for the hedging engine -- Phase 2a.

Still no broker call, no Telegram, no frontend: this module tracks the
PAPER position/capital state and decides WHEN a position should exit
(time cutoff, profit target, structural stop, or emergency), the same
"decide, don't execute" boundary Phase 1's selector kept. Persisted via
db.get_setting/set_setting (app.combos' exact pattern) under its own key.

Exit priority, explicit and documented (not incidental if-order): a
position can only close once, so when multiple conditions are true in
the same check, EMERGENCY beats everything, then STOP_LOSS (capital
preservation over letting a target-day play out), then PROFIT_TARGET,
then TIME_CUTOFF.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, time as dtime, timedelta, timezone

from .. import db
from . import capital as _capital

_KEY = "hedging_open_positions"
_IST = timezone(timedelta(hours=5, minutes=30))   # matches app.instruments/_market_calendar/market_hub
DEFAULT_EXIT_CUTOFF_IST = dtime(15, 15)   # "EXIT ALL by 3:15 PM" -- no carry-forward


@dataclass
class HedgePosition:
    position_id: str
    symbol: str
    primary_strike: float
    primary_option_type: str
    primary_entry_premium: float
    hedge_strike: float
    hedge_entry_premium: float
    lots: int
    lot_size: int
    max_loss_per_lot: float
    opened_at: str
    margin_locked: float
    cost_paid: float
    profit_target: float | None = None    # absolute rupees, combined P&L
    status: str = "OPEN"                  # OPEN | CLOSED
    closed_at: str | None = None
    exit_reason: str | None = None        # STOP_LOSS | PROFIT_TARGET | TIME_CUTOFF | EMERGENCY | MANUAL
    gross_pnl: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "HedgePosition":
        return cls(**d)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_all() -> dict[str, HedgePosition]:
    try:
        raw = db.get_setting(_KEY)
        d = json.loads(raw) if raw else {}
        return {k: HedgePosition.from_dict(v) for k, v in d.items()}
    except Exception:
        return {}


def _save_all(positions: dict[str, HedgePosition]) -> None:
    db.set_setting(_KEY, json.dumps({k: p.to_dict() for k, p in positions.items()}))


def open_position(*, symbol: str, primary_strike: float, primary_option_type: str,
                  primary_entry_premium: float, hedge_strike: float, hedge_entry_premium: float,
                  lots: int, lot_size: int, max_loss_per_lot: float, margin_locked: float,
                  cost_paid: float, profit_target: float | None = None) -> HedgePosition:
    """Opens a PAPER position -- locks margin + spends premium cost against
    the capital ledger via app.hedging.capital (raises the same ValueError
    that module raises if capital doesn't actually cover it; this function
    never silently opens a position the ledger can't afford)."""
    cap = _capital.load()
    _capital.allocate(cap, margin=margin_locked, cost=cost_paid)
    _capital.save(cap)

    pos = HedgePosition(
        position_id=str(uuid.uuid4()), symbol=symbol,
        primary_strike=primary_strike, primary_option_type=primary_option_type.upper(),
        primary_entry_premium=primary_entry_premium, hedge_strike=hedge_strike,
        hedge_entry_premium=hedge_entry_premium, lots=lots, lot_size=lot_size,
        max_loss_per_lot=max_loss_per_lot, opened_at=_now_iso(),
        margin_locked=margin_locked, cost_paid=cost_paid, profit_target=profit_target)

    positions = load_all()
    positions[pos.position_id] = pos
    _save_all(positions)
    return pos


def combined_pnl(position: HedgePosition, *, current_primary_premium: float,
                 current_hedge_premium: float) -> float:
    """Gross combined P&L (both legs, before any cost/margin release) for a
    SOLD primary + BOUGHT hedge: the primary gains as its premium falls,
    the hedge gains as its premium rises."""
    primary_pnl = (position.primary_entry_premium - current_primary_premium) * position.lot_size * position.lots
    hedge_pnl = (current_hedge_premium - position.hedge_entry_premium) * position.lot_size * position.lots
    return round(primary_pnl + hedge_pnl, 2)


def check_exit(position: HedgePosition, *, current_primary_premium: float,
              current_hedge_premium: float, now: datetime | None = None,
              cutoff: dtime = DEFAULT_EXIT_CUTOFF_IST, emergency: bool = False) -> dict:
    """Pure decision, never executes anything. `now` defaults to real IST
    time; pass it explicitly in tests/backtests to avoid depending on the
    wall clock. Priority: EMERGENCY > STOP_LOSS > PROFIT_TARGET >
    TIME_CUTOFF > none (still open)."""
    pnl = combined_pnl(position, current_primary_premium=current_primary_premium,
                       current_hedge_premium=current_hedge_premium)
    if emergency:
        return {"should_exit": True, "reason": "EMERGENCY", "pnl": pnl}

    max_loss = position.max_loss_per_lot * position.lots
    if pnl <= -max_loss:
        return {"should_exit": True, "reason": "STOP_LOSS", "pnl": pnl}

    if position.profit_target is not None and pnl >= position.profit_target:
        return {"should_exit": True, "reason": "PROFIT_TARGET", "pnl": pnl}

    now = now or datetime.now(_IST)
    now_ist = now.astimezone(_IST) if now.tzinfo else now.replace(tzinfo=_IST)
    if now_ist.time() >= cutoff:
        return {"should_exit": True, "reason": "TIME_CUTOFF", "pnl": pnl}

    return {"should_exit": False, "reason": None, "pnl": pnl}


def close_position(position: HedgePosition, *, exit_reason: str, pnl: float) -> HedgePosition:
    """Books the realized P&L, releases margin back to the capital ledger,
    and marks the position CLOSED. Idempotent-guarded: closing an
    already-closed position is a no-op (returns it unchanged) rather than
    double-crediting the ledger."""
    if position.status == "CLOSED":
        return position

    cap = _capital.load()
    _capital.release(cap, margin=position.margin_locked, realized_pnl=pnl)
    _capital.save(cap)

    position.status = "CLOSED"
    position.closed_at = _now_iso()
    position.exit_reason = exit_reason
    position.gross_pnl = pnl

    positions = load_all()
    positions[position.position_id] = position
    _save_all(positions)
    return position


def emergency_exit_all(*, current_prices: dict[str, tuple[float, float]]) -> list[dict]:
    """EXIT ALL, regardless of P&L/time. `current_prices`: {position_id:
    (current_primary_premium, current_hedge_premium)} -- a real quote is
    required per open position; a position with no quote available is
    reported UNRESOLVED rather than closed at a fabricated price. Returns
    one result dict per open position (closed or unresolved)."""
    positions = load_all()
    results = []
    for pid, pos in positions.items():
        if pos.status != "OPEN":
            continue
        quote = current_prices.get(pid)
        if quote is None:
            results.append({"position_id": pid, "status": "UNRESOLVED",
                           "reason": "no current quote available -- not closed at a fabricated price"})
            continue
        cur_primary, cur_hedge = quote
        decision = check_exit(pos, current_primary_premium=cur_primary,
                              current_hedge_premium=cur_hedge, emergency=True)
        close_position(pos, exit_reason="EMERGENCY", pnl=decision["pnl"])
        results.append({"position_id": pid, "status": "CLOSED", "pnl": decision["pnl"]})
    return results
