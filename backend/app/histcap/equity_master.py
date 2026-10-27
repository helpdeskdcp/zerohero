"""NSE cash-equity symbol -> broker token, from the instrument master (read-only, cached). Lives in histcap so the capture layer never imports the contest package."""
from __future__ import annotations

_tok_cache: dict = {}


def resolve(symbol: str, sdk=None) -> dict | None:
    """NSE cash-equity row of a trading symbol from the instrument master: {"symbol","token","name"} or None. Cached for the process lifetime (a token does not change)."""
    sym = symbol.strip().upper(); hit = _tok_cache.get(sym)
    if hit is not None: return hit or None
    if sdk is None:
        from .worker import CaptureWorker
        sdk = CaptureWorker._default_sdk()
    out = None
    try:
        for r in sdk.search_instruments(symbol=sym, exchange="NSE"):
            if str(r.get("symbol", "")).upper() == f"{sym}-EQ" and str(r.get("exch_seg", "NSE")).upper() == "NSE":
                out = {"symbol": sym, "token": str(r["token"]), "name": r.get("name") or sym}; break
    except Exception:
        return None                                                                                     # master unavailable right now: do not cache a "not found"
    _tok_cache[sym] = out or False
    return out
