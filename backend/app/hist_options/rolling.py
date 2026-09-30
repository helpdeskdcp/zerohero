"""Dhan expired-option history (POST /v2/charts/rollingoption) -> normalized rows.

Verified live 2026-09-30 (see backend/DHAN_CAPABILITY_MATRIX.md): index options
return 1m open/high/low/close/volume plus DIRECT iv, oi, strike, spot per bar,
ATM-relative strikes, years of depth. NOT provided: bid/ask/qty, Greeks, expiry
date, security_id. MCX (CRUDEOIL/NATURALGAS) returns empty -> reported as
UNSUPPORTED_BY_DHAN, never substituted.

Derived fields are labelled: oi_change / previous_oi come from consecutive bars
of the same series (oi_change_source=DERIVED). Greeks are RECONSTRUCTED only when
the caller supplies an expiry date; otherwise UNAVAILABLE(NO_EXPIRY_DATE).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Callable

import requests

from ..optionchain.sources import dhan_chain as _dc
from . import greeks_model as gm

_URL = "https://api.dhan.co/v2/charts/rollingoption"
_SEG = {"NIFTY": "NSE_FNO", "BANKNIFTY": "NSE_FNO", "FINNIFTY": "NSE_FNO", "MIDCPNIFTY": "NSE_FNO",
        "SENSEX": "BSE_FNO", "BANKEX": "BSE_FNO"}
_EXCH = {"NSE_FNO": "NFO", "BSE_FNO": "BFO"}
UNSUPPORTED_BY_DHAN = "UNSUPPORTED_BY_DHAN"


def support_status(underlying: str) -> str:
    u = underlying.upper()
    if u in _SEG:
        return "VERIFIED"
    if u in _dc._MCX_SYMBOLS:
        return UNSUPPORTED_BY_DHAN      # empty responses for every documented shape (2026-09-30)
    return "NOT_VERIFIED"


def fetch_side(underlying: str, option_type: str, from_date: str, to_date: str, *, strike: str = "ATM",
               interval: int = 1, expiry_flag: str = "WEEK", expiry_code: int = 1,
               timeout: float = 30.0, session=None) -> dict | None:
    """Raw {'timestamp':[...], 'close':[...], ...} for one side, or None on any
    auth/network/shape failure or unsupported underlying."""
    u = underlying.upper()
    if u not in _SEG or option_type not in ("CE", "PE"):
        return None
    headers = _dc._headers()
    if headers is None:
        return None
    body = {"exchangeSegment": _SEG[u], "interval": str(interval), "securityId": _dc._UNDERLYING_SCRIP[u],
            "instrument": "OPTIDX", "expiryFlag": expiry_flag, "expiryCode": expiry_code, "strike": strike,
            "drvOptionType": "CALL" if option_type == "CE" else "PUT",
            "requiredData": ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
            "fromDate": from_date, "toDate": to_date}
    try:
        r = (session or requests.Session()).post(_URL, json=body, headers=headers, timeout=timeout)
        if r.status_code != 200:
            return None
        side = ((r.json().get("data") or {}).get("ce" if option_type == "CE" else "pe")) or None
        return side if side and side.get("timestamp") else None
    except Exception:
        return None


def normalize(side: dict, *, underlying: str, option_type: str, interval_min: int = 1,
              expiry_for: Callable[[date], date | None] | None = None,
              r: float = 0.0, q: float = 0.0) -> list[dict]:
    """Bars -> rows in the spec's schema. `expiry_for(bar_date)` returns the
    expiry date in force for that bar (caller-owned assumption, recorded in
    inputs); None => Greeks UNAVAILABLE."""
    u = underlying.upper()
    ts = side.get("timestamp") or []
    n = len(ts)
    col = lambda k: side.get(k) or [None] * n  # noqa: E731
    o, h, l, c, v, oi, iv, k_, spot = (col(x) for x in ("open", "high", "low", "close", "volume",
                                                       "oi", "iv", "strike", "spot"))
    exch = _EXCH[_SEG[u]]
    tf = f"{interval_min}m"
    rows, prev_oi, prev_key = [], None, None
    for i in range(n):
        ts_utc = datetime.fromtimestamp(int(ts[i]), tz=timezone.utc)
        strike = k_[i]
        key = (ts_utc.date(), strike)        # OI delta only within the same day + same absolute strike
        cur_oi = oi[i]
        prev = prev_oi if key == prev_key else None
        prev_oi, prev_key = cur_oi, key
        ivd = (iv[i] / 100.0) if iv[i] not in (None, 0) else None
        exp = expiry_for(ts_utc.date()) if expiry_for else None
        if exp is not None and ivd and spot[i] and strike:
            T = max(1e-6, (datetime(exp.year, exp.month, exp.day, 10, 0, tzinfo=timezone.utc) - ts_utc)
                    .total_seconds() / (365.0 * 86400.0))       # 15:30 IST = 10:00 UTC
            g = gm.reconstruct(spot[i], strike, T, ivd, option_type == "CE", model="BS_CARRY", r=r, q=q,
                               iv_source="DIRECT")
            g["inputs"]["expiry_date"] = exp.isoformat()
        else:
            g = gm._unavailable("NO_EXPIRY_DATE" if exp is None else "MISSING_IV_OR_SPOT",
                                {"iv": ivd, "spot": spot[i], "strike": strike})
        rows.append({
            "timestamp": ts_utc.isoformat(), "timeframe": tf, "exchange": exch, "underlying": u,
            "expiry": exp.isoformat() if exp else None, "strike": strike, "option_type": option_type,
            "security_id": None, "spot": spot[i], "option_ltp": None, "bid": None, "ask": None,
            "bid_qty": None, "ask_qty": None, "volume": v[i], "oi": cur_oi, "previous_oi": prev,
            "oi_change": (cur_oi - prev) if (cur_oi is not None and prev is not None) else None,
            "oi_change_source": "DERIVED", "iv": ivd, "iv_source": "DIRECT" if ivd else UNAVAILABLE_IV,
            "delta": g["delta"], "gamma": g["gamma"], "theta": g["theta"], "vega": g["vega"],
            "greek_source": g["greek_source"], "greek_model": g["model"],
            "greek_inputs": g["inputs"], "greek_calc_ts": g["calculation_timestamp"],
            "vwap": None, "open": o[i], "high": h[i], "low": l[i], "close": c[i],
            "source": "DHAN_ROLLING"})
    return rows


UNAVAILABLE_IV = gm.UNAVAILABLE
