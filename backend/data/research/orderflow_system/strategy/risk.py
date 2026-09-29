"""Shared risk engine -- position sizing, partial booking, session filters,
max-concurrent/max-daily-loss circuit breakers. One instance per backtest
run, reset per session (spec Section 3, "shared risk engine")."""
from __future__ import annotations

from datetime import datetime, time, timedelta


def position_size(account_equity: float, risk_pct: float, r_points: float, lot_size: float) -> int:
    """Lots such that a full stop-out loses risk_pct of equity. Never
    returns a fractional lot; 0 if r_points is degenerate (stop == entry)."""
    if r_points <= 0:
        return 0
    rupee_risk = account_equity * risk_pct
    lots = rupee_risk / (r_points * lot_size)
    return max(int(lots), 0)


class RiskState:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._day = None
        self.daily_loss_r = 0.0
        self.open_positions = 0
        self.blocked_for_day = False

    def _roll_day(self, ts: datetime):
        day = ts.date()
        if day != self._day:
            self._day = day
            self.daily_loss_r = 0.0
            self.blocked_for_day = False

    def session_blackout(self, ts: datetime, session_open: time) -> bool:
        blackout_end = (datetime.combine(ts.date(), session_open, tzinfo=ts.tzinfo)
                        + timedelta(minutes=self.cfg["news_blackout_mins"]))
        return ts < blackout_end

    def can_enter(self, ts: datetime, session_open: time = time(9, 15)) -> bool:
        self._roll_day(ts)
        if self.blocked_for_day:
            return False
        if self.session_blackout(ts, session_open):
            return False
        if self.open_positions >= self.cfg["max_concurrent_positions"]:
            return False
        return True

    def on_entry(self):
        self.open_positions += 1

    def on_exit(self, ts: datetime, r_multiple: float):
        self._roll_day(ts)
        self.open_positions = max(0, self.open_positions - 1)
        if r_multiple < 0:
            self.daily_loss_r += abs(r_multiple)
        if self.daily_loss_r >= self.cfg["max_daily_loss_r"]:
            self.blocked_for_day = True


def apply_partial_booking(entry: float, stop: float, targets: list[float], direction: str,
                          cfg: dict) -> dict:
    """Returns the partial-booking plan: book cfg['partial_pct'] of the
    position at targets[0] (tp1), move stop to breakeven if
    move_stop_to_breakeven_on_partial, run the remainder to targets[1] (tp2)."""
    return {
        "tp1": targets[0], "tp1_fraction": cfg["partial_pct"],
        "tp2": targets[1], "tp2_fraction": 1.0 - cfg["partial_pct"],
        "breakeven_after_tp1": cfg["move_stop_to_breakeven_on_partial"],
        "breakeven_price": entry,
    }
