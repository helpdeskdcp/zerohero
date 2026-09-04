"""
Shared live-market context builder for the mathematical engines.

Read-only. Best-effort: any field the app can't supply is left absent, and the
consuming engine reports DATA_INSUFFICIENT with the exact missing fields — it is
never fabricated. Adds a per-call `data_quality` note (ACTUAL / MISSING).

Reused by:
  - mathematical_confluence.api  (/api/mathematics/*)
  - smart_index_scalper.scanner  (/api/smart-scalper/*)
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
_MKT = {"SENSEX": "BSE", "BANKEX": "BSE", "NATURALGAS": "MCX", "CRUDEOIL": "MCX"}

# tiny TTL cache so a ranking scan over 5 indices doesn't hammer the broker.
# The cold scan makes ~3 broker calls x 5 symbols (~21s); a keep-warm background
# scan can only bridge the gap if the TTL outlasts one scan + its poll interval,
# hence the 45s default (research/analysis surface — NOT the execution path).
import os as _os
_CACHE: dict[str, tuple[float, dict]] = {}
_TTL_SEC = max(2.0, float(_os.environ.get("CHANAKYA_MATH_CTX_TTL_SEC", "12")))
_STALE_MAX_SEC = 300.0   # how long a last-good context may be served if a live fetch fails
# previous-day OHLC does not change intraday: once fetched for an IST date, keep
# it all session so a flaky getCandleData call can't drop the whole context to
# DATA_INSUFFICIENT. {symbol: (ist_date, {high, low, close})}
_PREVDAY: dict[str, tuple[str, dict]] = {}


# ---- Phase 1: live spot from the shared WS feed (0 REST) --------------------
_IDX_TOK: dict[str, tuple[str, int]] = {}   # sym -> (token, exchange_type)
_SUBBED = False
_UNIVERSE = [s.strip().upper() for s in _os.environ.get(
    "SMART_SCALPER_UNIVERSE", "NIFTY,BANKNIFTY,FINNIFTY,MIDCPNIFTY,SENSEX,BANKEX").split(",") if s.strip()]


def _resolve_idx_token(sdk, sym: str):
    if sym in _IDX_TOK:
        return _IDX_TOK[sym]
    try:
        from ..connectors.angel_ws import EXCHANGE_TYPE
        idx = sdk.resolve_index(sym)
        if idx.get("status") == "OK" and idx.get("token"):
            et = EXCHANGE_TYPE.get(str(idx.get("exchange") or "NSE").upper(), 1)
            _IDX_TOK[sym] = (str(idx["token"]), et)
            return _IDX_TOK[sym]
    except Exception:
        pass
    return None


def _feed_spot(sym: str, sdk) -> float | None:
    """Live index LTP from the shared WS feed — no broker REST call. Subscribes
    the whole configured universe once so it doesn't depend on what autoscalp
    happens to be watching."""
    global _SUBBED
    try:
        from ..feed_registry import get_feed
        feed = get_feed()
        if not feed:
            return None
        if not _SUBBED:
            want = []
            for u in _UNIVERSE:
                tk = _resolve_idx_token(sdk, u)
                if tk:
                    want.append({"token": tk[0], "exchange_type": tk[1]})
            if want:
                feed.subscribe(want, owner="mathhub")
                _SUBBED = True
        tk = _resolve_idx_token(sdk, sym)
        return feed.get_ltp(tk[0]) if tk else None
    except Exception:
        return None


def _spot_from_histcap(symbol: str, max_age_sec: float = 180.0) -> dict | None:
    """Latest captured index/future quote from market_history.db — a broker-free
    spot when the live get_quote is being rate-limited. Returns
    {ltp, open, high, low} or None if nothing recent enough."""
    try:
        import sqlite3
        from ..histcap.store import DB_PATH as _HDB
        with sqlite3.connect(f"file:{_HDB}?mode=ro", uri=True, timeout=5) as c:
            c.row_factory = sqlite3.Row
            r = c.execute(
                "SELECT ltp, open, high, low, received_ts FROM quote_snapshots "
                "WHERE symbol=? AND kind IN ('INDEX','FUTURE') AND ltp IS NOT NULL "
                "ORDER BY received_ts DESC LIMIT 1", (symbol,)).fetchone()
        if not r:
            return None
        ts = datetime.fromisoformat(str(r["received_ts"]).replace("Z", "+00:00"))
        if (datetime.now(timezone.utc) - ts).total_seconds() > max_age_sec:
            return None
        return {"ltp": r["ltp"], "open": r["open"], "high": r["high"], "low": r["low"]}
    except Exception:
        return None


def _prevday_from_histcap(symbol: str, before_date: str) -> dict | None:
    """Aggregate the previous captured session's OHLC from market_history.db —
    a broker-free fallback when the live daily-candle call fails."""
    try:
        import sqlite3
        from ..histcap.store import DB_PATH as _HDB
        with sqlite3.connect(f"file:{_HDB}?mode=ro", uri=True, timeout=5) as c:
            c.row_factory = sqlite3.Row
            for kind in ("INDEX", "FUTURE"):
                r = c.execute(
                    "SELECT MAX(h) hi, MIN(l) lo FROM market_candles WHERE symbol=? AND kind=? "
                    "AND session_date_ist < ? GROUP BY session_date_ist "
                    "ORDER BY session_date_ist DESC LIMIT 1", (symbol, kind, before_date)).fetchone()
                if not r or r["hi"] is None:
                    continue
                lc = c.execute(
                    "SELECT c FROM market_candles WHERE symbol=? AND kind=? AND session_date_ist=("
                    "  SELECT MAX(session_date_ist) FROM market_candles WHERE symbol=? AND kind=? "
                    "  AND session_date_ist < ?) ORDER BY bar_start DESC LIMIT 1",
                    (symbol, kind, symbol, kind, before_date)).fetchone()
                return {"high": r["hi"], "low": r["lo"], "close": lc["c"] if lc else r["hi"]}
    except Exception:
        return None
    return None


def market_context(symbol: str, *, window: int = 6, use_cache: bool = True,
                   allow_rest_fallback: bool = True) -> dict:
    """Assemble the live-market context for `symbol`.

    Since the architecture audit (phase 2) this is a thin coalescing cache over
    `market_hub.snapshot()` — the hub reads spot from the shared WS feed and
    OI/bars from histcap's capture DB, with only a throttled REST fallback. This
    function keeps the same name/shape/cache for every existing caller
    (/api/mathematics/*, the ranking scanner).
    """
    sym = symbol.upper()
    if use_cache:
        hit = _CACHE.get(sym)
        if hit and time.time() - hit[0] < _TTL_SEC:
            return hit[1]

    try:
        from .. import market_hub
        ctx = market_hub.snapshot(sym, window=window, allow_rest_fallback=allow_rest_fallback)
    except Exception as e:                                      # pragma: no cover
        ctx = {"instrument": sym, "prev_day": {}, "bars": [], "chain": [],
               "data_quality": {"context_error": f"{type(e).__name__}: {e}"}}

    if use_cache:
        if ctx.get("spot") is not None:
            _CACHE[sym] = (time.time(), ctx)                    # cache only a context with live data
        else:
            prev = _CACHE.get(sym)                              # transient failure -> last good, marked stale
            if prev and time.time() - prev[0] < _STALE_MAX_SEC:
                stale = dict(prev[1])
                stale["stale"] = True
                stale["data_quality"] = {**stale.get("data_quality", {}),
                                         "freshness": "STALE (last good; live fetch failed)"}
                return stale
    return ctx
