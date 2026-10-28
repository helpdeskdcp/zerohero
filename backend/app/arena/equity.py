"""Arena equity (NSE cash, intraday paper): symbol lookup in the broker instrument master, last captured quote, the pre-submit check, and the 3-second watch registration.
Read-only broker calls through the shared market SDK; nothing here can place an order."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone

from .contracts import DB, NOT_FUTURE, _ro

_lq_cache: dict = {}
from ..histcap.equity_master import resolve  # noqa: E402,F401  (re-export)


def latest_quote(symbol: str, path: str | None = None) -> dict | None:
    """Newest captured quote of an equity via the (symbol, kind, exch_ts) index: {"ltp","bid","ask","t"} or None. Cached for 1 s."""
    key = (symbol, path); hit = _lq_cache.get(key)
    if hit and time.time() - hit[0] < 1.0: return hit[1]
    c = _ro(path)
    try:
        r = c.execute("SELECT ltp,bid,ask,received_ts FROM quote_snapshots WHERE symbol=? AND kind='EQUITY' AND ltp IS NOT NULL" + NOT_FUTURE + " ORDER BY exch_ts DESC LIMIT 1", (symbol,)).fetchone()
    finally:
        c.close()
    out = {"ltp": r[0], "bid": r[1], "ask": r[2], "t": datetime.fromisoformat(r[3].replace("Z", "+00:00")).timestamp()} if r else None
    _lq_cache[key] = (time.time(), out); return out


def watch(symbol: str, *, ttl_sec: float | None = None) -> dict:
    from ..histcap import priority
    return priority.request_watch_equity(symbol, ttl_sec=ttl_sec)


def check(symbol: str, side: str, entry: float, mode: str, *, sl: float | None = None, wait_sec: float = 6.0, path: str | None = None, sdk=None) -> dict:
    """-> {"found", "problems", "warnings", "last"}. `problems` make a scored equity signal unscorable (unknown symbol, or an entry that is a typo against the live price)."""
    out = {"found": False, "problems": [], "warnings": [], "last": None}
    if resolve(symbol, sdk) is None:
        out["problems"].append(f"{symbol} is not an NSE cash-equity trading symbol in the broker instrument master (use the NSE symbol, e.g. RELIANCE)"); return out
    out["found"] = True
    try: watch(symbol)                                                                                  # dense quotes from now until the close
    except Exception: pass
    deadline = time.time() + wait_sec; q = latest_quote(symbol, path)
    while (q is None or time.time() - q["t"] > 30) and time.time() < deadline:
        time.sleep(1.0); _lq_cache.pop((symbol, path), None); q = latest_quote(symbol, path)
    out["last"] = q
    if not q or time.time() - q["t"] > 120:
        out["warnings"].append("no live quote for this symbol yet: it was added to the 1-second capture, so quotes start within seconds; the entry could not be checked against the market"); return out
    r = entry / q["ltp"]
    if mode != "LIMIT" and abs(r - 1.0) > 0.10: out["problems"].append(f"entry {entry:g} is {abs(r - 1) * 100:.0f}% away from the last price {q['ltp']:g}: a {mode} entry must be near the market (typo?)")
    if mode == "LIMIT" and abs(r - 1.0) > 0.20: out["warnings"].append(f"limit {entry:g} is {abs(r - 1) * 100:.0f}% away from the last price {q['ltp']:g}")
    if q.get("bid") and q.get("ask") and q["ask"] > q["bid"]:
        spread = q["ask"] - q["bid"]
        if spread / q["ltp"] > 0.01: out["warnings"].append(f"wide bid/ask spread ({spread:.2f} = {100 * spread / q['ltp']:.1f}% of the price): fills will cost that much")
        if sl and 0 < abs(entry - sl) <= 1.5 * spread: out["warnings"].append(f"stop-loss {sl:g} is within 1.5x the bid/ask spread of the entry: it is likely to be hit immediately")
    return out


# ----------------------------------------------------------------------------- symbol search
import csv
import os
import threading

_idx_lock = threading.Lock(); _idx: list | None = None
_NIFTY50_CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "nifty_direction", "constituents.csv")


def _index(sdk=None) -> list:
    """Every NSE cash-equity symbol of the broker instrument master [{symbol, token, company, n50}], built once (about 2,700 rows). NIFTY 50 names carry their company name and rank first."""
    global _idx
    if _idx is not None: return _idx
    with _idx_lock:
        if _idx is not None: return _idx
        if sdk is None:
            from ..histcap.worker import CaptureWorker
            sdk = CaptureWorker._default_sdk()
        n50 = {}
        try:
            with open(_NIFTY50_CSV, encoding="utf-8") as f:
                for r in csv.DictReader(f): n50[(r.get("Symbol") or "").strip().upper()] = (r.get("Company Name") or "").strip()
        except Exception:
            pass
        rows = []
        for r in sdk.load_instrument_master():
            sy = str(r.get("symbol", ""))
            if r.get("exch_seg") == "NSE" and sy.endswith("-EQ"):
                nm = str(r.get("name") or sy[:-3]).upper(); rows.append({"symbol": nm, "token": str(r["token"]), "company": n50.get(nm, ""), "n50": nm in n50})
        rows.sort(key=lambda x: x["symbol"]); _idx = rows
    return _idx


def search(q: str, limit: int = 12, sdk=None) -> list[dict]:
    """Prefix matches first (NIFTY 50 before the rest, then A-Z), then names that merely contain the text. 'r' -> RELIANCE, RAILTEL ...; 'rel' -> RELIANCE, RELAXO ..."""
    s = (q or "").strip().upper()
    if not s: return []
    rows = _index(sdk); pre = [r for r in rows if r["symbol"].startswith(s)]; pre.sort(key=lambda r: (not r["n50"], r["symbol"]))
    out = pre[:limit]
    if len(out) < limit:
        sub = [r for r in rows if s in r["symbol"] and not r["symbol"].startswith(s)]; sub.sort(key=lambda r: (not r["n50"], r["symbol"])); out += sub[: limit - len(out)]
    return [{"symbol": r["symbol"], "company": r["company"], "token": r["token"]} for r in out]


# ----------------------------------------------------------------------------- live ticks (quote router)
_want: dict = {}                       # symbol -> last time a screen asked for it (epoch); idle symbols are dropped from the websocket after IDLE_SEC
IDLE_SEC, MAX_WATCH = 120.0, 40
_src_stats = {"ws": {"hits": 0, "errors": 0}, "db": {"hits": 0, "errors": 0}, "upstox": {"hits": 0, "errors": 0}}
EQ_OWNER = "arena_equity"


def _ws_feed():
    try:
        from ..feed_registry import get_feed
        return get_feed()
    except Exception:
        return None


def _sync_subscriptions(now: float, sdk=None) -> None:
    for s in [k for k, t in _want.items() if now - t > IDLE_SEC]: _want.pop(s, None)
    feed = _ws_feed()
    if feed is None: return
    want = []
    for s in sorted(_want, key=_want.get, reverse=True)[:MAX_WATCH]:
        r = resolve(s, sdk)
        if r: want.append({"token": r["token"], "exchange_type": 1})          # 1 = NSE cash
    try: feed.subscribe(want, owner=EQ_OWNER)                                  # idempotent: the feed only reconciles when the union changes
    except Exception: _src_stats["ws"]["errors"] += 1


def ticks(symbols: list[str], *, path: str | None = None, sdk=None, now: float | None = None) -> dict:
    """Latest price of each symbol from the FASTEST fresh source. 'ws' = Angel One websocket push (tick speed, LTP only), 'db' = the 3-second captured quote (LTP + bid / ask, what settlement uses).
    The websocket LTP wins when it is newer than the captured quote; bid / ask always come from the captured quote. A source that is down or stale is simply skipped."""
    now = now or time.time(); out = {}
    for s in symbols: _want[s] = now
    _sync_subscriptions(now, sdk); feed = _ws_feed()
    for s in symbols:
        r = resolve(s, sdk); rec = {"ltp": None, "bid": None, "ask": None, "t": None, "src": None}
        q = latest_quote(s, path)
        if q: rec.update(ltp=q["ltp"], bid=q["bid"], ask=q["ask"], t=q["t"], src="db"); _src_stats["db"]["hits"] += 1
        w = feed.ltp.get(r["token"]) if (feed is not None and r) else None
        if w and now - w["recv"] <= 5.0 and (rec["t"] is None or w["recv"] >= rec["t"]):
            rec.update(ltp=w["ltp"], t=w["recv"], src="ws"); _src_stats["ws"]["hits"] += 1
        out[s] = rec
    stale = [s for s, r in out.items() if r["t"] is None or now - r["t"] > FAILOVER_AGE_SEC]
    if stale and _failover_enabled() and _nse_open():                                                            # nothing fresh from Angel while the market is open: ask the second broker
        try:
            for s, p in _upstox_ltp(stale).items():
                out[s].update(ltp=p, t=now, src="upstox"); _src_stats["upstox"]["hits"] += 1
        except Exception:
            _src_stats["upstox"]["errors"] += 1
    for r in out.values():
        if r["t"] is not None: r["age_s"] = round(now - r["t"], 2)
    return out


def source_status() -> dict:
    f = _ws_feed()
    return {"ws_connected": bool(f is not None and getattr(f, "connected", False)), "watching": len(_want), "stats": _src_stats}


# ----------------------------------------------------------------------------- display-only failover: Upstox last price
# Used ONLY when neither the Angel websocket nor the 3-second tape has a price newer than FAILOVER_AGE_SEC while NSE is open. It feeds the order ticket's number and nothing else:
# settlement keeps reading the captured Angel tape, so a second broker's prices never enter a contest result. Kill switch ARENA_EQUITY_FAILOVER=0.
FAILOVER_AGE_SEC = 10.0
_UP_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "upstox_nse_eq_keys.json")
_UP_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
_up_keys: dict | None = None; _up_cache: dict = {}; _up_lock = threading.Lock()


def _upstox_keys() -> dict:
    """{NSE symbol: Upstox instrument_key} for cash equity, from Upstox's public instrument file; cached on disk for 7 days."""
    global _up_keys
    if _up_keys is not None: return _up_keys
    with _up_lock:
        if _up_keys is not None: return _up_keys
        try:
            if os.path.exists(_UP_FILE) and time.time() - os.path.getmtime(_UP_FILE) < 7 * 86400:
                _up_keys = json.load(open(_UP_FILE)); return _up_keys
        except Exception:
            pass
        import gzip, requests
        r = requests.get(_UP_URL, timeout=30); r.raise_for_status()
        rows = json.loads(gzip.decompress(r.content))
        keys = {x["trading_symbol"].upper(): x["instrument_key"] for x in rows if x.get("segment") == "NSE_EQ" and x.get("instrument_type") == "EQ" and x.get("instrument_key") and x.get("trading_symbol")}
        os.makedirs(os.path.dirname(_UP_FILE), exist_ok=True); tmp = _UP_FILE + ".tmp"; json.dump(keys, open(tmp, "w")); os.replace(tmp, _UP_FILE); _up_keys = keys
    return _up_keys


def _upstox_ltp(symbols: list[str]) -> dict:
    """{symbol: last_price} from Upstox (one request for all symbols; each answer cached 2 s). Raises on any failure; the caller treats that as 'no failover price'."""
    now = time.time(); out = {}; need = [s for s in symbols if not (_up_cache.get(s) and now - _up_cache[s][0] < 2.0)]
    if need:
        keys = _upstox_keys(); ik = {s: keys[s] for s in need if s in keys}
        if ik:
            from ..connectors import upstox as U
            j = (U.get_ltp(",".join(ik.values())).get("json") or {}).get("data") or {}
            by_tok = {v.get("instrument_token"): v.get("last_price") for v in j.values() if isinstance(v, dict)}
            for s, k in ik.items():
                p = by_tok.get(k)
                if p: _up_cache[s] = (now, float(p))
    return {s: _up_cache[s][1] for s in symbols if _up_cache.get(s) and now - _up_cache[s][0] < 5.0}


def _failover_enabled() -> bool:
    return os.environ.get("ARENA_EQUITY_FAILOVER", "1").strip().lower() not in ("0", "false", "off", "no")


def _nse_open() -> bool:
    try:
        from .. import market_calendar as mc
        return bool(mc.is_trading("NSE"))
    except Exception:
        return False
