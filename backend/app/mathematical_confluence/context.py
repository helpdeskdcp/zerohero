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
        sdk = _market_sdk(require_auth=False)
        if not sdk:
            ctx["data_quality"]["sdk"] = "MISSING (not authenticated)"
            return ctx
        mkt = _MKT.get(sym, "NSE")
        snap = market_data.selection_snapshot(
            sdk, mkt, sym, expiry="AUTO", option_type="BOTH", window=window,
            instrument="OPTION" if mkt in ("MCX", "BSE") else None)
        ctx["market"] = mkt
        ctx["spot"] = snap.get("spot") or snap.get("atm")
        ctx["atm"] = snap.get("atm")
        ctx["expiry"] = snap.get("expiry")
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

        und = snap.get("underlying_contract") or {}
        tok, exch = und.get("token"), und.get("exchange")
        if tok:
            now = datetime.now(IST)
            d = sdk.get_candles(exch, tok, "ONE_DAY",
                                (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M"),
                                now.strftime("%Y-%m-%d %H:%M"))
            cs = d.get("candles") or []
            if len(cs) >= 2:
                p = cs[-2]
                ctx["prev_day"] = {"high": p["high"], "low": p["low"], "close": p["close"]}
                ctx["data_quality"]["prev_day_ohlc"] = "ACTUAL"
            else:
                ctx["data_quality"]["prev_day_ohlc"] = "MISSING (<2 daily candles)"
            if cs:
                ctx["today_open"] = cs[-1]["open"]
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
        _CACHE[sym] = (time.time(), ctx)
    return ctx
