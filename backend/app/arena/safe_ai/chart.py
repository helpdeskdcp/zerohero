"""Safe U-turn overlay for the Terminal's underlying chart: where the rule (signals.detect) would have raised a CALL / PUT idea today, with the calibration base rate for that kind of marker. Read-only; the same pure rule the live
runner and the Pine mirror use. Ticks = today's stored index prices (+ the live spot), thinned to one per 30 s so a whole day costs well under a second; the result is cached for 15 s."""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from .. import contracts as CT
from ..jsonio import read_json_cached
from . import signals as SG

IST = timezone(timedelta(hours=5, minutes=30))
CALIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "data", "research", "safe_ai", "CALIBRATION.json")
STEP_S, COOLDOWN_S, MAX_MARKERS, SESSIONS = 30.0, 1800.0, 12, 3
MCX_UNDERLYINGS = ('NATURALGAS', 'CRUDEOIL', 'GOLDM')
MCX_NOTE = 'MCX shown for reference: the rule and its base rates were tested on NIFTY options only'


def params_for(sym: str) -> SG.Params:
    """NSE / BSE indices: the validated window 09:35-14:30. MCX (open until 23:30): 09:30-23:00."""
    return SG.Params(t_from=(9, 30), t_to=(23, 0), expiry_day_t_to=(23, 0)) if sym in MCX_UNDERLYINGS else SG.Params()
_cache: dict = {}


def _ts(s: str) -> float: return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _future_ticks(sym: str, day: str, path: str | None) -> list:
    """MCX underlyings have no index price: the FRONT future (nearest expiry on/after the day) stands in, on its exchange time axis (09:00-23:30)."""
    d0 = datetime.fromisoformat(day).replace(tzinfo=IST).replace(hour=9, minute=0).timestamp()
    lo = datetime.fromtimestamp(d0 - 1800, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"); hi = datetime.fromtimestamp(d0 + 15.5 * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    c = CT._ro(path)
    try: rows = c.execute("SELECT exch_ts, ltp, expiry FROM quote_snapshots WHERE symbol=? AND kind='FUTURE' AND exch_ts>=? AND exch_ts<=? AND ltp IS NOT NULL", (sym, lo, hi)).fetchall()
    finally: c.close()
    by: dict = {}
    for ts, px, exp in rows:
        try: iso = CT.norm_expiry(exp)
        except Exception: continue
        by.setdefault(iso, []).append((_ts(ts), float(px)))
    live = sorted(e for e in by if e >= day) or sorted(by)
    out = []
    for t, px in sorted(by[live[0]]) if live else []:
        if t >= d0 and (not out or t >= out[-1][0] + STEP_S): out.append((t, px))
    return out


def day_ticks(sym: str, day: str, path: str | None = None) -> list:
    """Stored index prices of one session through two indexed reads (a session-day scan reads ~100 000 rows)."""
    if sym in MCX_UNDERLYINGS: return _future_ticks(sym, day, path)
    d0 = datetime.fromisoformat(day).replace(tzinfo=IST).replace(hour=9, minute=15).timestamp()
    lo = datetime.fromtimestamp(d0 - 1800, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"); hi = datetime.fromtimestamp(d0 + 6.5 * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    c = CT._ro(path)
    try:
        ids = c.execute("SELECT MIN(id), MAX(id) FROM quote_snapshots WHERE session_date_ist=? AND symbol=?", (day, sym)).fetchone()
        rows = c.execute("SELECT received_ts, ltp FROM quote_snapshots WHERE symbol=? AND kind='INDEX' AND exch_ts>=? AND exch_ts<=? AND ltp IS NOT NULL", (sym, lo, hi)).fetchall()
        if ids[0] is not None: rows += c.execute("SELECT received_ts, ltp FROM quote_snapshots WHERE symbol=? AND kind='INDEX' AND exch_ts IS NULL AND id BETWEEN ? AND ? AND ltp IS NOT NULL", (sym, ids[0], ids[1])).fetchall()
    finally:
        c.close()
    out = []
    for t, p in sorted((_ts(r[0]), float(r[1])) for r in rows):
        if t >= d0 and (not out or t >= out[-1][0] + STEP_S): out.append((t, p))
    return out


def calibration() -> dict:
    d = read_json_cached(CALIB, {}); return d if isinstance(d, dict) else {}


def _bounds(label: str) -> tuple[float, float]:
    """'0.60-0.80%' -> (0.6, 0.8); '>= 1.00%' -> (1.0, inf)"""
    t = label.replace("%", "").replace(" ", "")
    if t.startswith(">="): return float(t[2:]), float("inf")
    a, b = t.split("-", 1); return float(a), float(b)


def _rate_for(thrust_pct: float, cal: dict) -> dict | None:
    for b in cal.get("by_thrust") or []:
        try: lo, hi = _bounds(str(b.get("label")))
        except Exception: continue
        if lo <= thrust_pct < hi and b.get("n"): return {"n": b["n"], "target_hit": b["target_hit"], "ci95": b["target_hit_ci95"], "net_per_trade": b["net_per_trade"], "bucket": b["label"]}
    o = cal.get("overall")
    return {"n": o["n"], "target_hit": o["target_hit"], "ci95": o["target_hit_ci95"], "net_per_trade": o["net_per_trade"], "bucket": "all"} if o and o.get("n") else None


def _markers_for_day(ticks: list, cal: dict, sym: str = "") -> list:
    out = []
    if len(ticks) < 20: return out
    p = params_for(sym); last = {}; mcx = sym in MCX_UNDERLYINGS
    for i in range(20, len(ticks)):
        t = ticks[i][0]
        if not SG.in_window(t, p): continue
        s = SG.detect(ticks[:i + 1], t, p)
        if not s or t - last.get(s["side"], -1e18) < COOLDOWN_S: continue
        last[s["side"]] = t; f = s["features"]
        out.append({"time": int(t), "side": s["side"], "thrust_pct": f["thrust_pct"], "recovery": f.get("recovery"), "reason": s["reason"], "base_rate": None if mcx else _rate_for(f["thrust_pct"], cal)})
    return out


def recent_days(now: float, n: int = SESSIONS) -> list[str]:
    """Today and the previous calendar days (weekends skipped), newest first: the sessions whose markers are shown (like the Winners markers, which span several days of candles)."""
    d = datetime.fromtimestamp(now, IST).date(); out = []
    for k in range(0, 9):
        x = d - timedelta(days=k)
        if x.weekday() < 5: out.append(x.isoformat())
        if len(out) >= n + 2: break
    return out


def overlay(sym: str, now: float | None = None, path: str | None = None, spot: float | None = None) -> dict:
    """-> {"markers": [{time (epoch, UTC), side CE|PE, thrust_pct, recovery, reason, base_rate}] over the last SESSIONS sessions, "state": where the rule stands now (words + session high / low), "calibration": {...}}"""
    now = now or time.time(); today = datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d"); cal = calibration(); markers = []; days_used = []; state = None; got = 0
    for day in recent_days(now):
        key = (sym, day, path); hit = _cache.get(key); ttl = 15.0 if day == today else 6 * 3600.0
        if hit and now - hit[0] < ttl: ticks, mk = hit[1], hit[2]
        else:
            ticks = day_ticks(sym, day, path); mk = _markers_for_day(ticks, cal, sym); _cache[key] = (now, ticks, mk)
        if len(ticks) < 20 and day != today: continue
        if got >= SESSIONS: break
        got += 1; days_used.append(day); markers += mk
        if state is None and len(ticks) >= 20:                                                              # the newest session that has data: describe it
            ref = min(now, ticks[-1][0] + 60.0); state = {**SG.explain(ticks, ref, params_for(sym)), "day": day, "is_today": day == today, "last_tick": int(ticks[-1][0])}
            if sym in MCX_UNDERLYINGS: state["status"] += " · " + MCX_NOTE
    markers.sort(key=lambda m: m["time"])
    return {"markers": markers[-MAX_MARKERS * SESSIONS:], "state": state, "days": days_used, "calibration": {"basis": cal.get("basis"), "overall": cal.get("overall"), "note": cal.get("note")}}
