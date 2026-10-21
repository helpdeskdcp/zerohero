"""Pure rule functions of H-EV-NG (constants fixed by the pre-registered spec, section C). Nothing here is tuned."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
TRIGGER_MOVE = 0.003                    # |close(R+2)/open(R) - 1| >= 0.30%
SL_FRAC, TARGET_FRAC = 0.70, 1.40       # -30% / +40% of the reference premium
MAX_SPREAD_FRAC = 0.08                  # bid/ask spread over mid
HOLD_MIN = 30                           # time exit at R + 30 min
QUOTE_WAIT_SEC = 90
INACTIVE_DATES = {"2026-11-26": "US Thanksgiving: the report moves to Wednesday (untested)"}
CAL_END = date(2027, 3, 7)              # US summer time starts 2027-03-14: the slot is not defined beyond this date


def release_slot(d: date) -> tuple[tuple[int, int] | None, str]:
    """(hour, minute) IST of the 10:30 New York release for Thursday `d`, or (None, reason) when the module is inactive."""
    if d.weekday() != 3: return None, "not a Thursday"
    if d.isoformat() in INACTIVE_DATES: return None, INACTIVE_DATES[d.isoformat()]
    if d > CAL_END: return None, "calendar not defined beyond 2027-03-07"
    return ((20, 0), "US summer time: 20:00 IST") if d <= date(2026, 10, 31) else ((21, 0), "US winter time: 21:00 IST (extrapolated, untested slot)")


def slot_epoch(d: date) -> float | None:
    s, _ = release_slot(d)
    return datetime(d.year, d.month, d.day, s[0], s[1], tzinfo=IST).timestamp() if s else None


def trigger(bars: list[tuple]) -> dict | None:
    """bars: the three 1-minute bars (start_ts, o, h, l, c) opening at R, R+1, R+2, in order. -> {'move', 'dir'} or None (no trigger)."""
    if len(bars) != 3 or not bars[0][1]: return None
    move = bars[2][4] / bars[0][1] - 1
    return {"move": move, "dir": "CE" if move > 0 else "PE"} if round(abs(move), 9) >= TRIGGER_MOVE else None


def nearest_strike(strikes: list[float], price: float) -> float:
    return min(strikes, key=lambda k: (abs(k - price), k))


def plan(ref_ask: float) -> dict:
    return {"sl": round(ref_ask * SL_FRAC, 2), "targets": [round(ref_ask * TARGET_FRAC, 2)]}


def spread_ok(bid: float | None, ask: float | None) -> bool:
    return bool(bid and ask and ask >= bid and (ask - bid) / ((ask + bid) / 2) <= MAX_SPREAD_FRAC)
