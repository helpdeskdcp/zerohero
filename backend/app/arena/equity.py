"""Arena equity (NSE cash, intraday paper): symbol lookup in the broker instrument master, last captured quote, the pre-submit check, and the 3-second watch registration.
Read-only broker calls through the shared market SDK; nothing here can place an order."""
from __future__ import annotations

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
        out["warnings"].append("no live quote for this symbol yet: it was added to the 3-second capture, so quotes start within seconds; the entry could not be checked against the market"); return out
    r = entry / q["ltp"]
    if mode != "LIMIT" and abs(r - 1.0) > 0.10: out["problems"].append(f"entry {entry:g} is {abs(r - 1) * 100:.0f}% away from the last price {q['ltp']:g}: a {mode} entry must be near the market (typo?)")
    if mode == "LIMIT" and abs(r - 1.0) > 0.20: out["warnings"].append(f"limit {entry:g} is {abs(r - 1) * 100:.0f}% away from the last price {q['ltp']:g}")
    if q.get("bid") and q.get("ask") and q["ask"] > q["bid"]:
        spread = q["ask"] - q["bid"]
        if spread / q["ltp"] > 0.01: out["warnings"].append(f"wide bid/ask spread ({spread:.2f} = {100 * spread / q['ltp']:.1f}% of the price): fills will cost that much")
        if sl and 0 < abs(entry - sl) <= 1.5 * spread: out["warnings"].append(f"stop-loss {sl:g} is within 1.5x the bid/ask spread of the entry: it is likely to be hit immediately")
    return out
