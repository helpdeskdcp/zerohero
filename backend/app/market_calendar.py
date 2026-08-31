"""Single source of truth for NSE / MCX session windows (IST).

Every timing consumer -- the freshness gate, both strategy safeguards, the WS
keep-alive, and the deploy/restart guard -- reads from here instead of its own
hard-coded `09:15 <= m <= 15:30`.

    segment_status(exchange, now=None)      -> PRE_OPEN | OPEN | POST_CLOSE | CLOSED
    is_trading(exchange, now=None, *, instrument=None) -> bool   (True only for OPEN)
    closed_regime(exchange, now=None)       -> "MARKET_CLOSED" | None
    restart_allowed(now=None)               -> (bool, reason)
    marks_label(token, ...) helpers live in instruments, not here.

Weekend -> CLOSED. Trading holidays: optional data/market_holidays.json holding
a JSON list of "YYYY-MM-DD" strings; if the file is absent, no holiday
suppression is applied (fail-open on the calendar, never on a live gate).

The window table is overridable via the CHANAKYA_MARKET_WINDOWS env var (JSON)
so exchange-notice changes (e.g. MCX evening close 23:30 <-> 23:55 on the US
DST switch) need a config push, not a code change.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone

_IST = timezone(timedelta(hours=5, minutes=30))


def _m(hh: int, mm: int) -> int:
    return hh * 60 + mm


# minute-of-day [start, end] windows, IST. Ordered by precedence within an
# exchange: the first window that contains `now` wins for OPEN; PRE_OPEN /
# POST_CLOSE are checked only if no OPEN window matches.
_DEFAULT_WINDOWS = {
    "NSE": {
        "pre_open":   [_m(9, 0),  _m(9, 15)],
        "open":       [_m(9, 15), _m(15, 30)],   # equity + F&O regular market
        "open_fno":   [_m(9, 15), _m(15, 30)],   # NSE F&O closes 15:30; 15:40 is a no-entry closing session
        "post_close": [_m(15, 40), _m(16, 0)],
    },
    "MCX": {
        "pre_open":   [_m(8, 45), _m(8, 59)],
        "open":       [_m(9, 0),  _m(23, 30)],   # morning + non-agri evening, continuous
        "open_agri":  [_m(9, 0),  _m(21, 0)],    # international agri commodities
        "post_close": [_m(23, 30), _m(23, 59)],
    },
    "BSE": {   # SENSEX / BSE F&O -- mirrors NSE
        "pre_open":   [_m(9, 0),  _m(9, 15)],
        "open":       [_m(9, 15), _m(15, 30)],
        "post_close": [_m(15, 40), _m(16, 0)],
    },
}

# maintenance windows in which a service restart / deploy is permitted, IST.
# NSE-only boxes: 15:50-16:15. Any box that also carries MCX must wait for the
# 23:35+ gap or the pre-dawn window.
_RESTART_WINDOWS = [
    [_m(15, 50), _m(16, 15)],   # NSE post-close
    [_m(23, 35), _m(23, 59)],   # after MCX evening close
    [_m(0, 0),   _m(8, 40)],    # overnight / pre-market
]


def _windows() -> dict:
    raw = os.environ.get("CHANAKYA_MARKET_WINDOWS")
    if not raw:
        return _DEFAULT_WINDOWS
    try:
        override = json.loads(raw)
        merged = {k: dict(v) for k, v in _DEFAULT_WINDOWS.items()}
        for ex, wins in (override or {}).items():
            merged.setdefault(ex.upper(), {}).update(wins or {})
        return merged
    except Exception:
        return _DEFAULT_WINDOWS


def _now_ist(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(_IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc).astimezone(_IST)
    return now.astimezone(_IST)


def _holidays() -> set[str]:
    path = os.environ.get("CHANAKYA_MARKET_HOLIDAYS",
                          os.path.join(os.path.dirname(__file__), "..", "data", "market_holidays.json"))
    try:
        with open(path) as fh:
            return {str(d)[:10] for d in json.load(fh)}
    except (OSError, ValueError):
        return set()


def is_holiday(d: date | None = None) -> bool:
    d = d or _now_ist().date()
    return d.isoformat() in _holidays()


def _in(mod: int, win) -> bool:
    return win is not None and win[0] <= mod < win[1]


def segment_status(exchange: str, now: datetime | None = None, *, instrument: str | None = None) -> str:
    """PRE_OPEN | OPEN | POST_CLOSE | CLOSED for `exchange` at `now` (default: live)."""
    ex = (exchange or "").upper()
    wins = _windows().get(ex)
    if not wins:
        return "CLOSED"
    dt = _now_ist(now)
    if dt.weekday() >= 5 or is_holiday(dt.date()):
        return "CLOSED"
    mod = dt.hour * 60 + dt.minute
    inst = (instrument or "").upper()
    open_key = "open"
    if ex == "MCX" and "AGRI" in inst:
        open_key = "open_agri"
    elif ex == "NSE" and ("OPT" in inst or "FUT" in inst or "FNO" in inst):
        open_key = "open_fno"
    if _in(mod, wins.get(open_key)) or _in(mod, wins.get("open")):
        return "OPEN"
    if _in(mod, wins.get("pre_open")):
        return "PRE_OPEN"
    if _in(mod, wins.get("post_close")):
        return "POST_CLOSE"
    return "CLOSED"


def is_trading(exchange: str, now: datetime | None = None, *, instrument: str | None = None) -> bool:
    return segment_status(exchange, now, instrument=instrument) == "OPEN"


def market_open_flag(exchange: str, now: datetime | None = None) -> bool | None:
    """Tri-state for the freshness schema: True OPEN, False PRE_OPEN/POST_CLOSE/
    CLOSED, None for an exchange with no window table."""
    ex = (exchange or "").upper()
    if ex not in _windows():
        return None
    return segment_status(ex, now) == "OPEN"


def closed_regime(exchange: str, now: datetime | None = None, *, instrument: str | None = None) -> str | None:
    """`"MARKET_CLOSED"` when the strategy engine should suspend, else None."""
    return None if is_trading(exchange, now, instrument=instrument) else "MARKET_CLOSED"


def restart_allowed(now: datetime | None = None) -> tuple[bool, str]:
    """True only inside a maintenance window with every configured exchange
    non-active (no OPEN / PRE_OPEN segment)."""
    dt = _now_ist(now)
    for ex in _windows():
        st = segment_status(ex, dt)
        if st in ("OPEN", "PRE_OPEN"):
            return False, f"{ex} is {st}"
    mod = dt.hour * 60 + dt.minute
    if dt.weekday() >= 5 or is_holiday(dt.date()):
        return True, "non-trading day"
    for w in _RESTART_WINDOWS:
        if w[0] <= mod < w[1]:
            return True, f"maintenance window {w[0]//60:02d}:{w[0]%60:02d}-{w[1]//60:02d}:{w[1]%60:02d}"
    return False, "outside maintenance windows (markets between segments)"


def status_all(now: datetime | None = None) -> dict:
    dt = _now_ist(now)
    return {
        "ist": dt.isoformat(timespec="seconds"),
        "segments": {ex: segment_status(ex, dt) for ex in _windows()},
        "restart_allowed": restart_allowed(dt)[0],
        "restart_reason": restart_allowed(dt)[1],
        "holiday": is_holiday(dt.date()),
    }
