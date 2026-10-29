"""The Safe AI entry rule: a CONFIRMED U-TURN at the session extreme (pure function of the index ticks of one session; no I/O).

Why this setup (see data/research/safe_ai/DESIGN.md): the institution trader's signals (Mohit, 38 posts) cluster in the first hour, near-the-money weekly options, mostly AGAINST the opening thrust. Expiry-day options have the most gamma, so a
turn is paid fastest, and the same gamma/theta punishes a wrong, slow or late entry. The rule therefore waits for the turn to be CONFIRMED instead of guessing the extreme, and refuses everything else.

DOWN-thrust U-turn -> buy a CALL (mirror image -> buy a PUT):
  1. thrust   : from the highest price before the session low to that low the index fell at least `thrust` (fraction), and the low is 8..90 minutes old
  2. recovery : price is back `retrace_min`..`retrace_max` of the thrust above the low (turn proven, room left to the old high)
  3. higher low: no new low during the last `hold_min` minutes
  4. momentum : price is above its value `mom1` and `mom2` minutes ago
Time window and caps are applied by the caller (live runner / replay)."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class Params:
    thrust: float = 0.0060        # minimum thrust (0.60 % of the index). v1 used 0.50 %; the 12-month calibration (CALIBRATION.md) found the 0.50-0.60 % band weak in the development half, so v2 starts at 0.60 % (a post-hoc choice: forward-test it)
    retrace_min: float = 0.35
    retrace_max: float = 0.80
    min_age_min: float = 8.0      # the extreme must be at least this old (a turn needs time to prove itself)
    max_age_min: float = 90.0
    hold_min: float = 6.0         # no new extreme in the last N minutes
    mom1: float = 3.0
    mom2: float = 8.0
    t_from: tuple = (9, 35)       # IST window in which a signal may be raised
    t_to: tuple = (14, 30)
    expiry_day_t_to: tuple = (13, 30)   # on the expiry day of the traded option: theta wins after this

    def as_dict(self) -> dict: return asdict(self)


def _px_at(ticks, t):
    """last price at or before t (ticks sorted by time) or None"""
    lo, hi = 0, len(ticks) - 1; ans = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if ticks[mid][0] <= t: ans = ticks[mid][1]; lo = mid + 1
        else: hi = mid - 1
    return ans


def in_window(now_ts: float, p: Params, expiry_day: bool = False) -> bool:
    d = datetime.fromtimestamp(now_ts, IST); hm = (d.hour, d.minute)
    return p.t_from <= hm <= (p.expiry_day_t_to if expiry_day else p.t_to)


def detect(ticks: list, now_ts: float, p: Params = Params()) -> dict | None:
    """ticks: [(epoch, price), ...] of ONE session, oldest first, up to now. -> {"side": "CE"|"PE", "reason": str, "features": {...}} or None."""
    if len(ticks) < 20: return None
    t0 = ticks[0][0]
    if now_ts - t0 < (p.min_age_min + p.hold_min) * 60: return None
    px = ticks[-1][1]
    for side, sign in (("CE", -1), ("PE", 1)):                       # CE after a down-thrust (sign -1), PE after an up-thrust (+1)
        ex_i = min(range(len(ticks)), key=lambda i: ticks[i][1]) if sign < 0 else max(range(len(ticks)), key=lambda i: ticks[i][1])
        ex_t, ex_px = ticks[ex_i]
        age = (now_ts - ex_t) / 60.0
        if not (p.min_age_min <= age <= p.max_age_min): continue
        before = ticks[:ex_i + 1]
        ref_px = max(x[1] for x in before) if sign < 0 else min(x[1] for x in before)
        thrust = (ref_px - ex_px) / ref_px if sign < 0 else (ex_px - ref_px) / ref_px
        if thrust < p.thrust: continue
        move = abs(ref_px - ex_px)
        rec = ((px - ex_px) / move) if sign < 0 else ((ex_px - px) / move)
        if not (p.retrace_min <= rec <= p.retrace_max): continue
        since = [x[1] for x in ticks if x[0] >= now_ts - p.hold_min * 60]
        if not since: continue
        if (sign < 0 and min(since) < ex_px) or (sign > 0 and max(since) > ex_px): continue          # a fresh extreme inside the hold window: not a turn yet
        a, b = _px_at(ticks, now_ts - p.mom1 * 60), _px_at(ticks, now_ts - p.mom2 * 60)
        if a is None or b is None: continue
        if sign < 0 and not (px > a and px > b): continue
        if sign > 0 and not (px < a and px < b): continue
        return {"side": side, "reason": f"{'down' if sign < 0 else 'up'}-thrust {thrust * 100:.2f}% to {ex_px:g} ({age:.0f} min ago), recovered {rec * 100:.0f}% of it, no new extreme for {p.hold_min:g} min, momentum confirms",
                "features": {"thrust_pct": round(thrust * 100, 3), "extreme": ex_px, "extreme_age_min": round(age, 1), "recovery": round(rec, 3), "ref_px": ref_px, "spot": px}}
    return None


@dataclass(frozen=True)
class SweepParams:
    excess: float = 0.0010        # price must trade beyond the prior-day extreme by at least 0.10 %
    min_age_min: float = 3.0
    max_age_min: float = 45.0
    back_inside: float = 0.0005   # and be back inside the level by 0.05 %
    hold_min: float = 4.0         # no fresh extreme in the last N minutes
    mom1: float = 3.0
    mom2: float = 8.0
    t_from: tuple = (9, 35)
    t_to: tuple = (14, 30)
    expiry_day_t_to: tuple = (13, 30)

    def as_dict(self) -> dict: return asdict(self)


def detect_sweep(ticks: list, now_ts: float, pdh: float, pdl: float, p: SweepParams = SweepParams()) -> dict | None:
    """Family F4: SWEEP AND REJECTION of the prior day's high / low. A break of the level by `excess` that is taken back inside within `max_age_min` and then confirmed (no fresh extreme, momentum away from the level)
    -> sweep of the HIGH = buy a PUT, sweep of the LOW = buy a CALL. Same ticks as detect()."""
    if len(ticks) < 20 or not pdh or not pdl: return None
    px = ticks[-1][1]
    for side, level, sign in (("PE", pdh, 1), ("CE", pdl, -1)):
        pts = [x for x in ticks if x[1] > level * (1 + p.excess)] if sign > 0 else [x for x in ticks if x[1] < level * (1 - p.excess)]
        if not pts: continue
        ex_t, ex_px = max(pts, key=lambda x: x[1]) if sign > 0 else min(pts, key=lambda x: x[1])
        age = (now_ts - ex_t) / 60.0
        if not (p.min_age_min <= age <= p.max_age_min): continue
        inside = (px < level * (1 - p.back_inside)) if sign > 0 else (px > level * (1 + p.back_inside))
        if not inside: continue
        recent = [x[1] for x in ticks if x[0] >= now_ts - p.hold_min * 60]
        if not recent or (sign > 0 and max(recent) > ex_px * 0.9999) or (sign < 0 and min(recent) < ex_px * 1.0001): continue
        a, b = _px_at(ticks, now_ts - p.mom1 * 60), _px_at(ticks, now_ts - p.mom2 * 60)
        if a is None or b is None: continue
        if (sign > 0 and not (px < a and px < b)) or (sign < 0 and not (px > a and px > b)): continue
        return {"side": side, "reason": f"{'high' if sign > 0 else 'low'} of the previous day ({level:g}) swept to {ex_px:g} ({age:.0f} min ago) and rejected, back inside, momentum confirms",
                "features": {"level": level, "extreme": ex_px, "extreme_age_min": round(age, 1), "overshoot_pct": round(abs(ex_px - level) / level * 100, 3), "spot": px}}
    return None


def explain(ticks: list, now_ts: float, p: Params = Params(), expiry_day: bool = False) -> dict:
    """Where the rule stands RIGHT NOW, in words (for the chart: an indicator that says nothing looks broken). The larger of the two thrusts of the session is described; the reason is the first condition that is not met yet."""
    out = {"status": "Not enough index prices yet today (about 10 minutes needed)", "side": None, "thrust_pct": None, "recovery": None, "extreme": None, "extreme_age_min": None, "session_high": None, "session_low": None,
           "need_pct": round(p.thrust * 100, 2), "in_window": in_window(now_ts, p, expiry_day), "active": False}
    if len(ticks) < 20: return out
    hi_i = max(range(len(ticks)), key=lambda i: ticks[i][1]); lo_i = min(range(len(ticks)), key=lambda i: ticks[i][1]); px = ticks[-1][1]
    out["session_high"], out["session_low"] = ticks[hi_i][1], ticks[lo_i][1]
    ref_d = max(x[1] for x in ticks[:lo_i + 1]); thr_d = (ref_d - ticks[lo_i][1]) / ref_d; ref_u = min(x[1] for x in ticks[:hi_i + 1]); thr_u = (ticks[hi_i][1] - ref_u) / ref_u
    down = thr_d >= thr_u; ex_i, ref, thr = (lo_i, ref_d, thr_d) if down else (hi_i, ref_u, thr_u); ex_px = ticks[ex_i][1]; age = (now_ts - ticks[ex_i][0]) / 60.0
    move = abs(ref - ex_px); rec = ((px - ex_px) / move) if down else ((ex_px - px) / move) if move else 0.0
    side_txt = "down" if down else "up"; idea = "CALL" if down else "PUT"
    out.update(side="CE" if down else "PE", thrust_pct=round(thr * 100, 3), recovery=round(rec, 3), extreme=ex_px, extreme_age_min=round(age, 1))
    sig = detect(ticks, now_ts, p)
    if sig: out.update(status=f"{idea} idea ACTIVE: {sig['reason']}", active=True)
    elif thr < p.thrust: out["status"] = f"Watching: the biggest move today is a {side_txt}-thrust of {thr * 100:.2f}% (needs {p.thrust * 100:.2f}% before a {idea} idea can form)"
    elif age < p.min_age_min: out["status"] = f"{side_txt.capitalize()}-thrust {thr * 100:.2f}% is big enough, but its extreme is only {age:.0f} min old (needs {p.min_age_min:g}); waiting for the turn"
    elif age > p.max_age_min: out["status"] = f"{side_txt.capitalize()}-thrust {thr * 100:.2f}% happened {age:.0f} min ago (more than {p.max_age_min:g}): too old to trade"
    elif rec < p.retrace_min: out["status"] = f"{side_txt.capitalize()}-thrust {thr * 100:.2f}% is big enough; waiting for the turn: recovered {rec * 100:.0f}% of the move (needs {p.retrace_min * 100:.0f}-{p.retrace_max * 100:.0f}%)"
    elif rec > p.retrace_max: out["status"] = f"{side_txt.capitalize()}-thrust {thr * 100:.2f}%: already recovered {rec * 100:.0f}% (more than {p.retrace_max * 100:.0f}%), the turn is done"
    else: out["status"] = f"{side_txt.capitalize()}-thrust {thr * 100:.2f}% and recovery {rec * 100:.0f}%: waiting for the last confirmations (no fresh extreme for {p.hold_min:g} min, momentum)"
    if not out["in_window"] and not sig: out["status"] += f" · outside the trading window {p.t_from[0]:02d}:{p.t_from[1]:02d}-{p.t_to[0]:02d}:{p.t_to[1]:02d}"
    return out
