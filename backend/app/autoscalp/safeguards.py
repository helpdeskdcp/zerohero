"""spec-15 hard safeguards for the autonomous runner.

Every check is automatic and configurable. `check_entry()` returns
(allow: bool, reason: str) and is called before EVERY new position. Existing
positions are never force-touched by these (except the kill switch's FLATTEN
policy, handled by the runner).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .. import db
from ..execution import killswitch

_IST = timezone(timedelta(hours=5, minutes=30))

DEFAULTS = {
    "max_daily_loss": 3000.0,        # rupees of realised P&L; halt new entries at -this
    "max_trades_per_day": 18,
    "max_concurrent": 3,             # one position per watchlist symbol (NIFTY / NG / CRUDE)
    "max_consecutive_losses": 4,
    "daily_cutoff_hhmm": "15:00",    # NSE: no new entries after this (IST)
    "session_start_hhmm": "09:20",
    "mcx_daily_cutoff_hhmm": "23:00",  # MCX: no new entries after this (close is 23:30)
    "max_feed_age_sec": 12,          # WS mark older than this -> fail closed
    "max_spread_pct": 1.2,           # option (ask-bid)/mid; proxy if quotes lack depth
    "min_option_premium": 8.0,
    "require_feed_connected": True,
    "allow_weekend": False,   # replay/paper-rehearsal only
}


def _mod_now():
    n = datetime.now(_IST)
    return n.hour * 60 + n.minute, n.weekday()


def _hhmm(s, default):
    try:
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return default


def _today_iso():
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


class Safeguards:
    def __init__(self, config: dict | None = None):
        self.cfg = {**DEFAULTS, **(config or {})}
        self.consecutive_losses = 0
        self._halt_reason = None            # sticky within a session once tripped

    # ---- outcome feedback (runner calls after each close) ----
    def on_trade_closed(self, pnl: float) -> None:
        if pnl is not None and pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    # ---- realised P&L today (paper) ----
    def _daily_realised(self) -> float:
        start = _today_iso()
        total = 0.0
        for r in db.list_scalp_signals(source="LIVE", status="CLOSED", limit=500):
            if (r.get("exit_ts") or r.get("created_ts") or "") >= start:
                try:
                    total += float(r.get("points") or 0) * float(r.get("_qty") or 1)
                except (TypeError, ValueError):
                    pass
        return total

    def _trades_today(self) -> int:
        start = _today_iso()
        return sum(1 for r in db.list_scalp_signals(source="LIVE", limit=500)
                   if (r.get("entry_ts") or "") >= start and r.get("decision") in ("BUY_CE", "BUY_PE"))

    # ---- the gate ----
    def check_entry(self, *, open_count: int, feed_connected: bool, feed_age_sec,
                    underlying: str, side: str, open_keys: set,
                    option_premium: float | None = None,
                    spread_pct: float | None = None,
                    realised_pnl_today: float | None = None,
                    exchange: str = "NSE") -> tuple[bool, str]:
        c = self.cfg
        mod, wd = _mod_now()
        ex = str(exchange or "NSE").upper()

        if killswitch.is_active():
            return False, f"kill switch active ({killswitch.state().get('reason')})"
        if wd >= 5 and not c.get("allow_weekend"):
            return False, "weekend"
        if not c.get("allow_weekend"):
            from .. import market_calendar
            if market_calendar.is_holiday():
                return False, "exchange holiday"
        # NB: the runner's _evaluate already suspends the strategy outside this
        # symbol's exchange hours; here we only enforce the operator window.
        if ex in ("NSE", "BSE"):
            if mod < _hhmm(c["session_start_hhmm"], 560):
                return False, "pre-session"
            if mod >= _hhmm(c["daily_cutoff_hhmm"], 900):
                return False, f"past daily cutoff {c['daily_cutoff_hhmm']}"
        else:                                    # MCX / other: exchange hours + a late cutoff
            if mod >= _hhmm(c.get("mcx_daily_cutoff_hhmm", "23:00"), 1380):
                return False, f"past MCX cutoff {c.get('mcx_daily_cutoff_hhmm', '23:00')}"

        if c["require_feed_connected"] and not feed_connected:
            return False, "market-data feed not connected"
        try:
            if feed_age_sec is None or float(feed_age_sec) > c["max_feed_age_sec"]:
                return False, f"stale feed ({feed_age_sec}s > {c['max_feed_age_sec']}s)"
        except (TypeError, ValueError):
            return False, "feed age unavailable"

        if open_count >= c["max_concurrent"]:
            return False, f"max concurrent {c['max_concurrent']}"
        if (underlying, side) in open_keys:
            return False, f"duplicate: {underlying} {side} already open"

        if self.consecutive_losses >= c["max_consecutive_losses"]:
            return False, f"max consecutive losses ({self.consecutive_losses})"
        if self._trades_today() >= c["max_trades_per_day"]:
            return False, f"max trades/day ({c['max_trades_per_day']})"

        pnl = realised_pnl_today if realised_pnl_today is not None else self._daily_realised()
        if pnl <= -abs(c["max_daily_loss"]):
            self._halt_reason = f"daily loss cap hit ({round(pnl)})"
            return False, self._halt_reason
        if self._halt_reason:
            return False, self._halt_reason

        if option_premium is not None and option_premium < c["min_option_premium"]:
            return False, f"premium {option_premium} < min {c['min_option_premium']}"
        if spread_pct is not None and spread_pct > c["max_spread_pct"]:
            return False, f"spread {spread_pct}% > {c['max_spread_pct']}%"

        return True, "ok"

    def status(self) -> dict:
        return {
            "consecutive_losses": self.consecutive_losses,
            "trades_today": self._trades_today(),
            "realised_pnl_today": round(self._daily_realised(), 2),
            "halt_reason": self._halt_reason,
            "kill_switch": killswitch.state(),
            "config": self.cfg,
        }
