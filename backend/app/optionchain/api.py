"""
Layer 6 -- the read-only Option-Chain dashboard route.

GET /api/optionchain/underlyings            -> the supported list, grouped by
                                               exchange (NSE indices + BSE
                                               SENSEX/BANKEX + MCX CRUDEOIL/
                                               NATURALGAS). MCX/BSE are quote-only
                                               (LTP/OI, no broker Greeks).
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
from .history_analytics import oi_profile as _oi_profile
from .history_analytics import straddle_pnl as _straddle_pnl
from .history_analytics import vol_surface as _vol_surface
from .qualify import qualify as _qualify
from .resolve import get_chain
from .structure import analyze as _analyze_structure

router = APIRouter(prefix="/api/optionchain", tags=["optionchain"])

# Everything IDaddy captures option snapshots for (quote_snapshots kind='OPTION').
# Only the NSE indices also have broker Greeks (option_greeks) -> for MCX / BSE the
# chain is quote-only: LTP / bid / ask / OI, no Greeks / IV / GEX. angelone_chain
# already assembles all of these; the network fallbacks (Upstox / NSE) are
# NSE-index only, so MCX / BSE resolve from the captured data alone.
_GROUPS = {
    "NSE": ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"],
    "BSE": ["SENSEX", "BANKEX"],
    "MCX": ["CRUDEOIL", "NATURALGAS"],
}
SUPPORTED = [s for g in _GROUPS.values() for s in g]
_TTL = 20.0
_cache: dict = {}


@router.get("/underlyings")
def api_optionchain_underlyings():
    return {"underlyings": SUPPORTED, "groups": _GROUPS, "default": "NIFTY",
            "expiries": ["AUTO", "NEXT", "LATEST"],
            "note": "MCX/BSE are quote-only (LTP/OI, no Greeks); network fallback is NSE-index only"}


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
        "expiry_ctx": st.expiry_context or chain.expiry_ctx,
        "available_expiries": chain.available_expiries,
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


# --------------------------------------------------------------------------- #
#  Time-series analytics -- built on ALREADY-CAPTURED histcap data only:      #
#  no new broker fetch, no order path. See history_analytics.py docstring.    #
# --------------------------------------------------------------------------- #
_hist_cache: dict = {}
_HIST_TTL = 30.0


def _hist_cached(key, fn):
    now = time.time()
    hit = _hist_cache.get(key)
    if hit and now - hit["ts"] < _HIST_TTL:
        return hit["data"]
    data = _pack(fn())
    _hist_cache[key] = {"ts": now, "data": data}
    return data


@router.get("/{underlying}/vol-surface")
def api_vol_surface(underlying: str, max_expiries: int = 6):
    u = str(underlying or "").upper()
    max_expiries = max(1, min(12, int(max_expiries)))
    return _hist_cached(("vs", u, max_expiries),
                         lambda: _vol_surface(u, max_expiries=max_expiries))


@router.get("/{underlying}/straddle-pnl")
def api_straddle_pnl(underlying: str, expiry: str, entry_ts: str | None = None,
                     strike: float | None = None):
    u = str(underlying or "").upper()
    return _hist_cached(("sp", u, expiry, entry_ts, strike),
                         lambda: _straddle_pnl(u, expiry, entry_ts=entry_ts, strike=strike))


@router.get("/{underlying}/oi-profile")
def api_oi_profile(underlying: str, expiry: str, top_n_strikes: int = 8):
    u = str(underlying or "").upper()
    top_n_strikes = max(1, min(20, int(top_n_strikes)))
    return _hist_cached(("op", u, expiry, top_n_strikes),
                         lambda: _oi_profile(u, expiry, top_n_strikes=top_n_strikes))
