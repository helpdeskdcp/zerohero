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
STEP_S, COOLDOWN_S, MAX_MARKERS = 30.0, 1800.0, 12
_cache: dict = {}


def _ts(s: str) -> float: return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def day_ticks(sym: str, day: str, path: str | None = None) -> list:
    """Stored index prices of one session through two indexed reads (a session-day scan reads ~100 000 rows)."""
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


def overlay(sym: str, now: float | None = None, path: str | None = None, spot: float | None = None) -> dict:
    """-> {"markers": [{time (epoch, UTC), side CE|PE, thrust_pct, recovery, reason, base_rate}], "calibration": {...}}"""
    now = now or time.time(); day = datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d"); key = (sym, day, path)
    hit = _cache.get(key)
    if hit and now - hit[0] < 15.0 and spot is None: return hit[1]
    cal = calibration(); ticks = day_ticks(sym, day, path)
    if spot and ticks and now - ticks[-1][0] >= STEP_S: ticks.append((now, float(spot)))
    res = {"markers": [], "calibration": {"basis": cal.get("basis"), "overall": cal.get("overall"), "note": cal.get("note")}, "ticks": len(ticks)}
    if len(ticks) >= 20:
        p = SG.Params(); last = {}
        for i in range(20, len(ticks)):
            t = ticks[i][0]
            if not SG.in_window(t, p): continue
            s = SG.detect(ticks[:i + 1], t, p)
            if not s or t - last.get(s["side"], -1e18) < COOLDOWN_S: continue
            last[s["side"]] = t; f = s["features"]
            res["markers"].append({"time": int(t), "side": s["side"], "thrust_pct": f["thrust_pct"], "recovery": f.get("recovery"), "reason": s["reason"], "base_rate": _rate_for(f["thrust_pct"], cal)})
        res["markers"] = res["markers"][-MAX_MARKERS:]
    if spot is None: _cache[key] = (now, res)
    return res
