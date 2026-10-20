"""Which option contracts have captured quotes (market_history.db quote_snapshots) for the latest session, and the last quote of one contract. Used to validate submissions BEFORE they
become unscorable (wrong expiry, uncaptured strike, premium wildly off the market). READ-ONLY, cached 120 s, indexed queries only (session_date_ist, symbol)."""
from __future__ import annotations

import os
import sqlite3
import time

from .submit import norm_expiry

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "market_history.db")
_cache: dict = {}


def _ro(path):
    return sqlite3.connect(f"file:{os.path.abspath(path or DB)}?mode=ro", uri=True, timeout=20)


def latest_day(path: str | None = None) -> str | None:
    c = _ro(path)
    try:
        r = c.execute("SELECT MAX(session_date_ist) FROM quote_snapshots").fetchone()
    finally:
        c.close()
    return r[0] if r else None


def captured(underlying: str, day: str | None = None, path: str | None = None) -> dict:
    """{"day":..., "expiries": {iso: {"strikes": [...], "raw": 'DDMONYYYY'}}}"""
    key = (underlying, day, path); hit = _cache.get(key)
    if hit and time.time() - hit[0] < 120: return hit[1]
    c = _ro(path)
    try:
        if day:
            d = day
            rows = c.execute("SELECT expiry, strike FROM quote_snapshots WHERE session_date_ist=? AND symbol=? AND kind='OPTION' AND expiry IS NOT NULL GROUP BY expiry, strike", (d, underlying)).fetchall()
        else:                                                    # latest polls only: a PRIMARY KEY range scan (the last ~30k rows span several full-chain cycles), not a scan of the 12 GB table
            mx = c.execute("SELECT MAX(id) FROM quote_snapshots").fetchone()[0] or 0
            rows = c.execute("SELECT expiry, strike FROM quote_snapshots WHERE id>? AND symbol=? AND kind='OPTION' AND expiry IS NOT NULL GROUP BY expiry, strike", (mx - 30000, underlying)).fetchall()
            d = c.execute("SELECT session_date_ist FROM quote_snapshots WHERE id=?", (mx,)).fetchone()
            d = d[0] if d else None
    finally:
        c.close()
    ex: dict = {}
    for raw, k in rows:
        try: iso = norm_expiry(raw)
        except Exception: continue
        e = ex.setdefault(iso, {"strikes": set(), "raw": raw}); e["strikes"].add(float(k))
    out = {"day": d, "expiries": {i: {"strikes": sorted(v["strikes"]), "raw": v["raw"]} for i, v in sorted(ex.items())}}
    _cache[key] = (time.time(), out); return out


def last_quote(underlying: str, expiry_iso: str, strike: float, ot: str, day: str | None = None, path: str | None = None) -> dict | None:
    from .settle import snap_expiry
    c = _ro(path)
    try:
        d = day or latest_day(path)
        r = c.execute("SELECT ltp,bid,ask,received_ts FROM quote_snapshots WHERE session_date_ist=? AND symbol=? AND kind='OPTION' AND expiry=? AND strike=? AND option_type=? AND ltp IS NOT NULL ORDER BY id DESC LIMIT 1",
                      (d, underlying, snap_expiry(expiry_iso), strike, ot)).fetchone()
    finally:
        c.close()
    return {"ltp": r[0], "bid": r[1], "ask": r[2], "ts": r[3]} if r else None


def check(underlying: str, expiry_iso: str, strike: float, ot: str, entry: float, mode: str, *, day: str | None = None, path: str | None = None) -> dict:
    """-> {"found": bool, "problems": [..], "warnings": [..], "available_expiries": [...], "strike_range": [lo, hi], "last": {...}|None}. `problems` make a scored submission unscorable."""
    cap = captured(underlying, day, path); ex = cap["expiries"]; out = {"found": False, "problems": [], "warnings": [], "available_expiries": list(ex), "strike_range": None, "last": None, "day": cap["day"]}
    if expiry_iso not in ex:
        out["problems"].append(f"no quotes are captured for {underlying} expiry {expiry_iso}; captured expiries: {', '.join(ex) or 'none'}")
        return out
    ks = ex[expiry_iso]["strikes"]; out["strike_range"] = [ks[0], ks[-1]]
    if float(strike) not in ks:
        out["problems"].append(f"strike {strike:g} is not captured for {underlying} {expiry_iso} (captured {ks[0]:g}-{ks[-1]:g})"); return out
    out["found"] = True; q = last_quote(underlying, expiry_iso, float(strike), ot, day, path); out["last"] = q
    if q and q.get("ltp"):
        r = entry / q["ltp"]
        if mode != "LIMIT" and abs(r - 1.0) > 0.5: out["warnings"].append(f"entry {entry:g} is far from the last traded price {q['ltp']:g} ({mode} entries are meant to be near the market)")
        if mode == "LIMIT" and r < 0.4: out["warnings"].append(f"limit {entry:g} is {100 * (1 - r):.0f}% below the last price {q['ltp']:g}: it will probably never fill")
        if mode == "ABOVE" and r < 0.9: out["warnings"].append(f"ABOVE {entry:g} is already below the market ({q['ltp']:g}): it would trigger immediately")
    return out
