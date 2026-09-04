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
_TTL_SEC = max(5.0, float(_os.environ.get("CHANAKYA_MATH_CTX_TTL_SEC", "45")))
_STALE_MAX_SEC = 300.0   # how long a last-good context may be served if a live fetch fails
# previous-day OHLC does not change intraday: once fetched for an IST date, keep
# it all session so a flaky getCandleData call can't drop the whole context to
# DATA_INSUFFICIENT. {symbol: (ist_date, {high, low, close})}
_PREVDAY: dict[str, tuple[str, dict]] = {}


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


def market_context(symbol: str, *, window: int = 6, use_cache: bool = True) -> dict:
    sym = symbol.upper()
    if use_cache:
        hit = _CACHE.get(sym)
        if hit and time.time() - hit[0] < _TTL_SEC:
            return hit[1]

    ctx: dict = {"instrument": sym, "prev_day": {}, "bars": [], "chain": [],
                 "data_quality": {}}
    try:
        from ..connectors.angelone import _market_sdk
        from .. import market_data
        # require_auth=True so a stale daily REST token is refreshed here (the
        # login is serialised behind _sdk_lock — no stampede). With require_auth
        # =False this path could never self-heal and every /api/mathematics/* +
        # /api/smart-scalper/* call returned DATA_INSUFFICIENT until a restart.
        sdk = _market_sdk(require_auth=True)
        if not sdk:
            ctx["data_quality"]["sdk"] = "MISSING (broker auth unavailable)"
            return ctx
        mkt = _MKT.get(sym, "NSE")
        snap = market_data.selection_snapshot(
            sdk, mkt, sym, expiry="AUTO", option_type="BOTH", window=window,
            instrument="OPTION" if mkt in ("MCX", "BSE") else None)
        ctx["market"] = mkt
        ctx["spot"] = snap.get("spot") or snap.get("atm")
        ctx["atm"] = snap.get("atm")
        ctx["expiry"] = snap.get("expiry")
        # live get_quote gets rate-limited under the app's call volume — fall
        # back to histcap's latest captured index/future quote for the spot
        if ctx["spot"] is None:
            hq = _spot_from_histcap(sym)
            if hq and hq.get("ltp") is not None:
                ctx["spot"] = hq["ltp"]
                ctx["data_quality"]["spot"] = "ACTUAL (histcap fallback)"
                if hq.get("open") is not None:
                    ctx.setdefault("today_open", hq["open"])
                if hq.get("high") is not None and hq.get("low") is not None:
                    ctx.setdefault("day_high", hq["high"])
                    ctx.setdefault("day_low", hq["low"])
        ctx["chain"] = [
            {"strike": r.get("strike"), "expiry": snap.get("expiry"),
             "ce_ltp": r.get("ce_ltp"), "ce_oi": r.get("ce_oi"),
             "ce_oi_change": r.get("ce_oi_change"), "ce_volume": r.get("ce_volume"),
             "ce_oi_status": r.get("ce_oi_status"), "ce_token": r.get("ce_token"),
             "ce_delta": r.get("ce_delta"), "ce_gamma": r.get("ce_gamma"),
             "ce_theta": r.get("ce_theta"), "ce_vega": r.get("ce_vega"), "ce_iv": r.get("ce_iv"),
             "ce_greeks_source": r.get("ce_greeks_source"),
             "pe_ltp": r.get("pe_ltp"), "pe_oi": r.get("pe_oi"),
             "pe_oi_change": r.get("pe_oi_change"), "pe_volume": r.get("pe_volume"),
             "pe_oi_status": r.get("pe_oi_status"), "pe_token": r.get("pe_token"),
             "pe_delta": r.get("pe_delta"), "pe_gamma": r.get("pe_gamma"),
             "pe_theta": r.get("pe_theta"), "pe_vega": r.get("pe_vega"), "pe_iv": r.get("pe_iv"),
             "pe_greeks_source": r.get("pe_greeks_source")}
            for r in (snap.get("chain") or [])
        ]
        ctx["oi_coverage"] = snap.get("oi_coverage")
        ctx["data_quality"]["option_chain"] = ("ACTUAL" if ctx["chain"] else "MISSING")

        now = datetime.now(IST)
        today = now.strftime("%Y-%m-%d")
        und = snap.get("underlying_contract") or {}
        tok, exch = und.get("token"), und.get("exchange")

        # ---- previous-day OHLC: day-cache -> live daily candle -> histcap ----
        pd_cached = _PREVDAY.get(sym)
        if pd_cached and pd_cached[0] == today:
            ctx["prev_day"] = dict(pd_cached[1])
            ctx["data_quality"]["prev_day_ohlc"] = "ACTUAL (day-cached)"
        if tok:
            if not ctx.get("prev_day"):
                d = sdk.get_candles(exch, tok, "ONE_DAY",
                                    (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M"),
                                    now.strftime("%Y-%m-%d %H:%M"))
                cs = d.get("candles") or []
                if len(cs) >= 2:
                    p = cs[-2]
                    ctx["prev_day"] = {"high": p["high"], "low": p["low"], "close": p["close"]}
                    ctx["data_quality"]["prev_day_ohlc"] = "ACTUAL"
                    ctx["today_open"] = cs[-1]["open"]
                    _PREVDAY[sym] = (today, dict(ctx["prev_day"]))
        if not ctx.get("prev_day"):
            hc = _prevday_from_histcap(sym, today)
            if hc:
                ctx["prev_day"] = hc
                ctx["data_quality"]["prev_day_ohlc"] = "ACTUAL (histcap fallback)"
                _PREVDAY[sym] = (today, dict(hc))
            else:
                ctx["data_quality"]["prev_day_ohlc"] = "MISSING (daily candle + histcap both empty)"
        if tok:
            i5 = sdk.get_candles(exch, tok, "FIVE_MINUTE",
                                 now.strftime("%Y-%m-%d 09:00"), now.strftime("%Y-%m-%d %H:%M"))
            bars = [{"high": c["high"], "low": c["low"], "close": c["close"],
                     "volume": c.get("volume")} for c in (i5.get("candles") or [])]
            ctx["bars"] = bars
            ctx["data_quality"]["intraday_bars"] = ("ACTUAL" if bars else "MISSING")
            if bars:
                ctx["day_high"] = max(b["high"] for b in bars)
                ctx["day_low"] = min(b["low"] for b in bars)
                closes = [b["close"] for b in bars]
                ctx["mom_3m"] = round(closes[-1] - closes[-4], 4) if len(closes) >= 4 else 0.0
                vols = [b["volume"] for b in bars if b.get("volume")]
                if len(vols) >= 6:
                    ctx["current_volume"] = vols[-1]
                    ctx["avg_volume"] = sum(vols[-20:-1]) / max(1, len(vols[-20:-1]))
        else:
            ctx["data_quality"]["underlying_token"] = "MISSING"
    except Exception as e:                                      # pragma: no cover
        ctx["data_quality"]["context_error"] = f"{type(e).__name__}: {e}"

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
