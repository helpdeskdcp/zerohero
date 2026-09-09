"""
Layer 6 -- the read-only Option-Chain dashboard route.

GET /api/optionchain/underlyings            -> the supported list
GET /api/optionchain/{underlying}           -> {chain, analytics, structure,
                                                quality, qualification}
    ?expiry=AUTO|NEXT|LATEST|15SEP2026
    ?live=1        include the keyless Upstox / NSE fallback + greek-merge
    ?atm_window=12 strikes each side of ATM for the coverage / PCR window
    ?realized_vol=0.11   optional -> enables the IV-vs-realised block
    ?baseline=<ISO ts>   optional -> per-strike ΔOI vs an earlier captured snap

No order path, no `live_trading`, no API keys (Upstox/NSE are keyless, Angel
uses the existing capture DB). A short in-process cache keeps repeated view
polls cheap. Mounted from app.main like every other engine router.
"""
from __future__ import annotations

import time

from fastapi import APIRouter

from .analytics import compute_all, oi_change_vs_baseline
from .resolve import get_chain
from .structure import analyze as _analyze_structure
from .qualify import qualify as _qualify

router = APIRouter(prefix="/api/optionchain", tags=["optionchain"])

SUPPORTED = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
_TTL = 20.0
_cache: dict = {}


@router.get("/underlyings")
def api_optionchain_underlyings():
    return {"underlyings": SUPPORTED, "default": "NIFTY",
            "expiries": ["AUTO", "NEXT", "LATEST"]}


def _pack(v):
    return v.to_dict() if hasattr(v, "to_dict") else v


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


@router.get("/{underlying}")
def api_optionchain(underlying: str, expiry: str = "AUTO", live: int = 1,
                    atm_window: int = 12, realized_vol: float | None = None,
                    baseline: str | None = None):
    u = str(underlying or "").upper()
    exp = str(expiry or "AUTO").upper()
    allow_net = bool(int(live))
    try:
        atm_window = max(1, min(40, int(atm_window)))
    except (TypeError, ValueError):
        atm_window = 12
    realized_vol = _f(realized_vol)
    key = (u, exp, int(allow_net), atm_window, realized_vol)
    now = time.time()

    if not baseline:
        hit = _cache.get(key)
        if hit and now - hit["ts"] < _TTL:
            return hit["data"]

    chain = get_chain(u, exp, allow_network=allow_net, atm_window=atm_window)
    if chain is None or not chain.rows:
        out = {"status": "NO_DATA", "underlying": u, "expiry": exp,
               "note": "no captured chain and no network source returned data"}
        if not baseline:
            _cache[key] = {"ts": now, "data": out}
        return out

    analytics = {k: _pack(v) for k, v in
                 compute_all(chain, atm_window=atm_window).items()}
    st = _analyze_structure(chain, realized_vol=realized_vol)
    qual = _qualify(st)                       # read-only: no external signal / ANN here

    out = {
        "status": "OK",
        "underlying": u, "expiry": chain.expiry, "ts": chain.ts,
        "source": chain.source, "spot": chain.spot, "atm_strike": chain.atm_strike,
        "chain": chain.to_dict(),
        "analytics": analytics,
        "structure": st.to_dict(),
        "quality": chain.quality,
        "qualification": qual.to_dict(),
        "capability": chain.capability,
    }

    if baseline:
        try:
            base = get_chain(u, chain.expiry, allow_network=False, at_ts=baseline,
                             atm_window=atm_window)
            out["oi_baseline"] = (oi_change_vs_baseline(chain, base) if base
                                  else {"status": "no_baseline",
                                        "note": f"no captured snapshot at/<= {baseline}"})
        except Exception as e:                # a bad ts must not 500 the view
            out["oi_baseline"] = {"status": "error", "detail": f"{type(e).__name__}: {e}"}
    else:
        _cache[key] = {"ts": now, "data": out}
    return out
