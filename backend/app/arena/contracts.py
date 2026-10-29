"""Which option contracts have captured quotes (market_history.db quote_snapshots) for the latest session, and the last quote of one contract. Used to validate submissions BEFORE they
become unscorable (wrong expiry, uncaptured strike, premium wildly off the market). READ-ONLY, cached 120 s, indexed queries only (session_date_ist, symbol)."""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime

from .settle import FUTURE_STAMP_SEC
from .submit import norm_expiry
from .jsonio import read_json, write_json

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "market_history.db")
_cache: dict = {}

# A frame whose exchange stamp is ahead of its receive time (mis-stamped, see settle.FUTURE_STAMP_SEC) must never be shown as the 'latest' quote.
NOT_FUTURE = f" AND (exch_ts IS NULL OR strftime('%s',exch_ts)-strftime('%s',received_ts)<={FUTURE_STAMP_SEC})"
# Underlying SPOT (ATM detection only) tolerates a larger exch-vs-receive skew: when a capture cycle runs for minutes, received_ts is the cycle start while the broker stamp is taken later in the cycle (27-Oct-2026: every NG future frame was 150-350 s "ahead" -> NO_SPOT -> empty strike list). A price a few minutes old is fine for finding the ATM; settlement and last_quote keep the strict 30 s guard.
SPOT_STAMP_SEC = 900
NOT_FUTURE_SPOT = f" AND (exch_ts IS NULL OR strftime('%s',exch_ts)-strftime('%s',received_ts)<={SPOT_STAMP_SEC})"


def _ro(path):
    return sqlite3.connect(f"file:{os.path.abspath(path or DB)}?mode=ro", uri=True, timeout=20)


def latest_day(path: str | None = None) -> str | None:
    c = _ro(path)
    try:
        r = c.execute("SELECT MAX(session_date_ist) FROM quote_snapshots").fetchone()
    finally:
        c.close()
    return r[0] if r else None


def _compute_captured(underlying: str, day: str | None = None, path: str | None = None) -> dict:
    """{"day":..., "expiries": {iso: {"strikes": [...], "raw": 'DDMONYYYY'}}}"""
    key = (underlying, day, path)
    c = _ro(path)
    try:
        if day:
            d = day
            rows = c.execute("SELECT expiry, strike FROM quote_snapshots WHERE session_date_ist=? AND symbol=? AND kind='OPTION' AND expiry IS NOT NULL GROUP BY expiry, strike", (d, underlying)).fetchall()
        else:                                                    # latest polls only: a PRIMARY KEY range scan (the last ~30k rows span several full-chain cycles), not a scan of the 12 GB table
            mx = c.execute("SELECT MAX(id) FROM quote_snapshots").fetchone()[0] or 0
            rows = c.execute("SELECT expiry, strike FROM quote_snapshots WHERE id>? AND symbol=? AND kind='OPTION' AND expiry IS NOT NULL GROUP BY expiry, strike", (mx - 60000, underlying)).fetchall()
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




_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "arena", "captured_cache.json")
_refreshing: set = set(); _rlock = threading.Lock(); _file_loaded = [False]


from .markets import IST as _IST  # noqa: E402


def live_expiries(cap: dict, today: str | None = None) -> dict:
    """Only expiries that can still be traded: on or after TODAY's IST date. The captured list is built from the newest quote rows, which after a night / weekend / expiry still belong to the previous
    session, so yesterday's expiry (2026-10-27 on 2026-10-28) used to be listed first and became the default. An expiry dated today stays (it trades until the close). Sorted ascending, so the
    first entry is always the current expiry."""
    t = today or datetime.now(_IST).strftime("%Y-%m-%d")
    return {**cap, "expiries": {e: v for e, v in sorted(cap.get("expiries", {}).items()) if e >= t}}


def _load_file() -> None:
    """Cold start: the previous answer (a few minutes old at worst) is served at once while a fresh one is computed in the background."""
    if _file_loaded[0]: return
    _file_loaded[0] = True
    d = read_json(_CACHE_FILE, {})
    if isinstance(d, dict):
        for u, v in d.items(): _cache.setdefault((u, None, None), (0.0, v))


def _persist() -> None:
    try:
        d = {k[0]: v[1] for k, v in _cache.items() if k[1] is None and k[2] is None}
        write_json(_CACHE_FILE, d)
    except Exception:
        pass


def _refresh(key) -> None:
    try:
        _compute_captured(*key)
        if key[1] is None and key[2] is None: _persist()
    except Exception:
        pass
    finally:
        with _rlock: _refreshing.discard(key)


def captured(underlying: str, day: str | None = None, path: str | None = None) -> dict:
    """{"day":..., "expiries": {iso: {"strikes": [...], "raw": 'DDMONYYYY'}}}. Stale-while-revalidate: an answer older than 120 s is returned immediately and refreshed in the background (one refresher per key),
    so no request ever waits for the scan of the big quote table, except the very first one ever."""
    key = (underlying, day, path)
    if path is None: _load_file()
    hit = _cache.get(key)
    if hit:
        if time.time() - hit[0] >= 120:
            with _rlock:
                start = key not in _refreshing
                if start: _refreshing.add(key)
            if start: threading.Thread(target=_refresh, args=(key,), daemon=True).start()
        return live_expiries(hit[1]) if day is None else hit[1]
    out = _compute_captured(underlying, day, path)
    if path is None and day is None: _persist()
    return live_expiries(out) if day is None else out


def captured_fresh(underlying: str, day: str | None = None, path: str | None = None, max_age: float = 30.0) -> dict:
    """For decisions that cannot tolerate a stale answer (a channel post that creates a scored trade): the cached answer is used only if younger than `max_age` seconds, otherwise it is recomputed
    SYNCHRONOUSLY. captured() may hand out a day-old list right after an expiry roll (the first post after the roll lost a real SENSEX signal on 2026-10-23 that way)."""
    key = (underlying, day, path)
    if path is None: _load_file()
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < max_age: return live_expiries(hit[1]) if day is None else hit[1]
    out = _compute_captured(underlying, day, path)
    if path is None and day is None: _persist()
    return live_expiries(out) if day is None else out


def last_quote(underlying: str, expiry_iso: str, strike: float, ot: str, day: str | None = None, path: str | None = None) -> dict | None:
    from .settle import snap_expiry
    c = _ro(path)
    try:
        d = day or latest_day(path)
        r = c.execute("SELECT ltp,bid,ask,received_ts FROM quote_snapshots WHERE session_date_ist=? AND symbol=? AND kind='OPTION' AND expiry=? AND strike=? AND option_type=? AND ltp IS NOT NULL" + NOT_FUTURE + " ORDER BY id DESC LIMIT 1",
                      (d, underlying, snap_expiry(expiry_iso), strike, ot)).fetchone()
    finally:
        c.close()
    return {"ltp": r[0], "bid": r[1], "ask": r[2], "ts": r[3]} if r else None


def check(underlying: str, expiry_iso: str, strike: float, ot: str, entry: float, mode: str, *, sl: float | None = None, day: str | None = None, path: str | None = None) -> dict:
    """-> {"found": bool, "problems": [..], "warnings": [..], "available_expiries": [...], "strike_range": [lo, hi], "last": {...}|None}. `problems` make a scored submission unscorable."""
    cap = captured(underlying, day, path); ex = cap["expiries"]
    if expiry_iso not in ex or float(strike) not in ex[expiry_iso]["strikes"]:        # a stale cached list must never be the reason a real contract is called unscorable: look again, fresh
        cap = captured_fresh(underlying, day, path); ex = cap["expiries"]
    out = {"found": False, "problems": [], "warnings": [], "available_expiries": list(ex), "strike_range": None, "last": None, "day": cap["day"]}
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
        if sl and q.get("bid") and q.get("ask") and q["ask"] > q["bid"]:
            spread = q["ask"] - q["bid"]
            if 0 < entry - sl <= 1.5 * spread: out["warnings"].append(f"stop-loss {sl:g} is only {entry - sl:.2f} below the entry, about {(entry - sl) / spread:.1f}x the current bid/ask spread ({spread:.2f}): it is likely to be hit immediately")
        if mode == "ABOVE" and r < 0.9: out["warnings"].append(f"ABOVE {entry:g} is already below the market ({q['ltp']:g}): it would trigger immediately")
    return out


def spot_for(underlying: str, option_expiry_iso: str | None = None, path: str | None = None) -> tuple[float | None, str]:
    """Underlying price for ATM detection: the INDEX quote for index options; for MCX the FUTURE whose expiry is the nearest one on/after the option expiry. Latest polls only (PK range)."""
    c = _ro(path)
    try:
        mx = c.execute("SELECT MAX(id) FROM quote_snapshots").fetchone()[0] or 0
        r = c.execute("SELECT ltp, received_ts FROM quote_snapshots WHERE id>? AND symbol=? AND kind='INDEX' AND ltp IS NOT NULL" + NOT_FUTURE_SPOT + " ORDER BY id DESC LIMIT 1", (mx - 60000, underlying)).fetchone()
        if r: return float(r[0]), f"INDEX @ {r[1]}"
        rows = c.execute("SELECT expiry, ltp, received_ts, id FROM quote_snapshots WHERE id>? AND symbol=? AND kind='FUTURE' AND ltp IS NOT NULL" + NOT_FUTURE_SPOT + " ORDER BY id DESC", (mx - 60000, underlying)).fetchall()
    finally:
        c.close()
    best: dict = {}
    for raw, ltp, ts, _ in rows:
        try: iso = norm_expiry(raw)
        except Exception: continue
        best.setdefault(iso, (float(ltp), ts))
    if not best: return _feed_spot(underlying)
    pick = next((e for e in sorted(best) if not option_expiry_iso or e >= option_expiry_iso), sorted(best)[0])
    return best[pick][0], f"FUTURE {pick} @ {best[pick][1]}"


_spot_feed_cache: dict = {}
INDEX_UNDERLYINGS = ("NIFTY", "BANKNIFTY", "SENSEX")


def _feed_spot(underlying: str) -> tuple[float | None, str]:
    """Last resort when the stored quotes have no usable index price (the REST quote path returned API_ERROR rows): the live WebSocket index price from the shared Angel feed. Without a spot the chain cannot find the ATM,
    shows no ladder, registers no strike for the tick tape, and the order form has nothing to pick. Cached 2 s; index options only."""
    if underlying not in INDEX_UNDERLYINGS: return None, "NO_SPOT"
    hit = _spot_feed_cache.get(underlying)
    if hit and time.time() - hit[0] < 2.0: return hit[1]
    out = (None, "NO_SPOT")
    try:
        from ..connectors.angelone import _market_sdk
        from ..mathematical_confluence import context as _ctx
        v = _ctx._feed_spot(underlying, _market_sdk(require_auth=True))
        if v: out = (float(v), "WS FEED (shared Angel stream)")
    except Exception:
        pass
    _spot_feed_cache[underlying] = (time.time(), out); return out


_chain_cache: dict = {}


def chain(underlying: str, expiry_iso: str, option_type: str, n: int = 7, path: str | None = None) -> dict:
    hit = _chain_cache.get((underlying, expiry_iso, option_type, n, path))
    if hit and time.time() - hit[0] < 10: return hit[1]
    out = _chain(underlying, expiry_iso, option_type, n, path); _chain_cache[(underlying, expiry_iso, option_type, n, path)] = (time.time(), out); return out


def warm(underlyings=("NIFTY", "BANKNIFTY", "SENSEX", "NATURALGAS", "CRUDEOIL", "GOLDM"), path: str | None = None) -> None:
    """Touch the latest quote pages of every underlying so the first user click is fast (cold page cache on the 12 GB file costs seconds). Never raises."""
    for u in underlyings:
        try:
            cap = captured(u, None, path); e = next(iter(cap["expiries"]), None)
            if e: chain(u, e, "CE", 7, path)
        except Exception:
            continue


def _chain(underlying: str, expiry_iso: str, option_type: str, n: int = 7, path: str | None = None) -> dict:
    """ATM + n ITM + n OTM strikes (ITM = below spot for CE, above spot for PE) with the last quote of each. Everything from captured data; nothing is guessed."""
    from .settle import snap_expiry
    cap = captured(underlying, None, path); ex = cap["expiries"].get(expiry_iso)
    out = {"underlying": underlying, "expiry": expiry_iso, "option_type": option_type, "spot": None, "spot_source": None, "step": None, "atm": None, "rows": [], "problems": []}
    if not ex: out["problems"].append(f"no captured quotes for {underlying} {expiry_iso}"); return out
    spot, src = spot_for(underlying, expiry_iso, path); out["spot"], out["spot_source"] = spot, src
    ks = ex["strikes"]
    if spot is None: out["problems"].append("no underlying price captured: cannot detect ATM"); return out
    near = [k for k in ks if abs(k - spot) <= 0.15 * spot] or ks
    diffs = sorted({round(b - a, 6) for a, b in zip(near, near[1:]) if b > a}); step = diffs[0] if diffs else None
    if len(diffs) > 1:                                                                   # modal step near the money
        from collections import Counter
        step = Counter(round(b - a, 6) for a, b in zip(near, near[1:]) if b > a).most_common(1)[0][0]
    atm = min(ks, key=lambda k: abs(k - spot)); out["step"], out["atm"] = step, atm
    i = ks.index(atm); lo, hi = max(0, i - n), min(len(ks), i + n + 1); win = ks[lo:hi]
    c = _ro(path)
    try:
        mx = c.execute("SELECT MAX(id) FROM quote_snapshots").fetchone()[0] or 0
        qs: dict = {}
        for k, ltp, bid, ask, ts in c.execute("SELECT strike, ltp, bid, ask, received_ts FROM quote_snapshots WHERE id>? AND symbol=? AND kind='OPTION' AND expiry=? AND option_type=? AND strike BETWEEN ? AND ? AND ltp IS NOT NULL" + NOT_FUTURE + " ORDER BY id DESC",
                                              (mx - 60000, underlying, snap_expiry(expiry_iso), option_type, win[0], win[-1])):
            qs.setdefault(float(k), {"ltp": ltp, "bid": bid, "ask": ask, "ts": ts})
    finally:
        c.close()
    for k in win:
        d = ks.index(k) - i                                                              # >0 : strike above ATM
        if d == 0: lab = "ATM"
        else:
            itm = (d < 0) if option_type == "CE" else (d > 0); lab = ("ITM" if itm else "OTM") + str(abs(d))
        out["rows"].append({"strike": k, "label": lab, "quote": qs.get(k)})
    return out


_lq_cache: dict = {}


def latest_by(where: str, params: tuple, path: str | None, cache: dict, key) -> dict | None:
    """Newest captured quote selected by `where` (constant SQL text built by the callers, values in `params`) via the (symbol, ..., exch_ts) indexes: {"ltp","bid","ask","t"} or None. Cached for 1 s per `key` (many screens, one
    lookup); the cache is emptied if it ever grows past 2000 keys. Shared by the option and the equity quote lookups."""
    hit = cache.get(key)
    if hit and time.time() - hit[0] < 1.0: return hit[1]
    c = _ro(path)
    try:
        r = c.execute("SELECT ltp,bid,ask,received_ts FROM quote_snapshots WHERE " + where + " AND ltp IS NOT NULL" + NOT_FUTURE + " ORDER BY exch_ts DESC LIMIT 1", params).fetchone()
    finally:
        c.close()
    out = {"ltp": r[0], "bid": r[1], "ask": r[2], "t": datetime.fromisoformat(r[3].replace("Z", "+00:00")).timestamp()} if r else None
    if len(cache) > 2000: cache.clear()
    cache[key] = (time.time(), out); return out


def latest_quote(underlying: str, expiry_iso: str, strike: float, ot: str, path: str | None = None) -> dict | None:
    """Newest captured quote of ONE contract: {"ltp","bid","ask","t"} or None."""
    from .settle import snap_expiry
    return latest_by("symbol=? AND expiry=? AND strike=? AND option_type=? AND kind='OPTION' AND source IN ('ANGELONE_QUOTE_FULL','ANGELONE_WS_SNAP')", (underlying, snap_expiry(expiry_iso), float(strike), ot), path, _lq_cache,
                     (underlying, expiry_iso, float(strike), ot, path))
