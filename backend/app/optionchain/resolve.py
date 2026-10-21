"""Provider-aware chain resolution.

Default: Dhan live/captured -> AngelOne captured. Explicit provider selects
only its own sources; an as-of query always disables live network access.
Expiry aliases are resolved within each provider. Missing Greeks stay missing,
never borrowed from another provider. `prefer` remains a legacy ordering hint;
use `provider` for a strict contract. All reads retain source provenance.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .chain import OptionChain
from .quality import score as _quality
from .sources import angelone_chain, dhan_chain, dhan_captured, nse_v3, upstox_open

_ORDER = ("dhan_chain", "angelone_captured", "upstox_open", "nse_v3")
_FETCH = {
    "dhan_captured": lambda u, e, kw: dhan_captured.fetch(u, e, db_path=kw.get("db_path"),
        at_ts=kw.get("at_ts"), expiry_dates=kw.get("expiry_dates")),
    "dhan_chain": lambda u, e, kw: dhan_chain.fetch(u, e),
    "angelone_captured": lambda u, e, kw: angelone_chain.fetch(u, e, db_path=kw.get("db_path"),
        at_ts=kw.get("at_ts"), expiry_dates=kw.get("expiry_dates")),
    "upstox_open": lambda u, e, kw: upstox_open.fetch(u, e, session=kw.get("session")),
    "nse_v3": lambda u, e, kw: nse_v3.fetch(u, e, session=kw.get("session")),
}


def _merge_greeks(dst: OptionChain, src: OptionChain) -> int:
    idx = {(round(r.strike, 2), s): leg
           for r in src.rows for s, leg in (("CE", r.ce), ("PE", r.pe)) if leg}
    n = 0
    for r in dst.rows:
        for s, leg in (("CE", r.ce), ("PE", r.pe)):
            if not leg or leg.delta is not None:
                continue
            g = idx.get((round(r.strike, 2), s))
            if g and g.delta is not None:
                leg.delta, leg.gamma, leg.theta, leg.vega = g.delta, g.gamma, g.theta, g.vega
                if leg.iv is None:
                    leg.iv = g.iv
                n += 1
    return n


def get_chain(underlying: str, expiry: str = "AUTO", *, prefer: str | None = None,
              allow_network: bool = True, atm_window: int = 10, provider: str | None = None, **kw) -> OptionChain | None:
    # An as-of query can never be answered by a live network response.
    if kw.get("at_ts") is not None:
        from ..histcap.read_contract import instant
        try:
            instant(kw["at_ts"])
        except (ValueError, TypeError):
            return None
        allow_network = False
    if provider is not None:
        p = str(provider).upper()
        if p not in ("DHAN", "ANGELONE"):
            return None
        order = (["dhan_chain", "dhan_captured"] if allow_network else ["dhan_captured"]) if p == "DHAN" else ["angelone_captured"]
    elif prefer in _ORDER:
        # Existing prefer is an ordering hint, not an explicit provider constraint.
        order = [prefer] + [s for s in _ORDER if s != prefer]
    else:
        order = ["dhan_chain", "dhan_captured", "angelone_captured"]
    if not allow_network:
        order = [s for s in order if s in ("dhan_captured", "angelone_captured")]

    chain = None
    tried = []
    for name in order:
        try:
            p = "DHAN" if name.startswith("dhan") else "ANGELONE" if name.startswith("angelone") else name
            requested_expiry = kw.get("provider_expiries", {}).get(p, kw.get("expiry_request", expiry))
            if not allow_network:
                # Live discovery overrides cannot smuggle today's calendar into
                # an offline/as-of alias request. Explicit dates remain exact.
                requested_expiry = expiry
            c = _FETCH[name](underlying, requested_expiry, kw)
        except Exception as e:                    # a source must never break the resolver
            tried.append(f"{name}: ERR {type(e).__name__}")
            continue
        tried.append(f"{name}: {'ok(' + str(len(c.rows)) + ')' if c and c.rows else 'empty'}")
        if c and c.rows:
            chain = c
            break
    if chain is None:
        return None

    # A provider-constrained chain must not borrow Greeks from another broker.
    selected_provider = "DHAN" if chain.source.startswith("dhan") else "ANGELONE" if chain.source.startswith("angelone") else chain.source
    chain.capability["provider"] = selected_provider
    chain.capability["fallback_used"] = provider is None and selected_provider == "ANGELONE"
    if chain.capability["fallback_used"]:
        chain.capability["fallback_reason"] = "DHAN_UNAVAILABLE"
    for row in chain.rows:
        for leg in (row.ce, row.pe):
            if leg is not None and leg.provenance is None:
                leg.provenance = {"provider": selected_provider, "source": chain.source,
                                  "expiry": chain.expiry, "timestamp": chain.ts,
                                  "fallback_used": chain.capability["fallback_used"]}
            if leg is not None:
                leg.provenance.update(fallback_used=chain.capability["fallback_used"],
                    fallback_reason=chain.capability.get("fallback_reason"), requested_as_of=kw.get("at_ts"))

    chain.notes.append("resolve order: " + " -> ".join(tried))
    chain.quality = _quality(chain, atm_window=atm_window)
    chain.notes.append(f"resolved @ {datetime.now(timezone.utc).isoformat()}")
    return chain


DEFAULT_CONFLICT_TOLERANCE_PCT = 0.5


def check_data_conflict(underlying: str, *, tolerance_pct: float = DEFAULT_CONFLICT_TOLERANCE_PCT) -> dict:
    """MCP 3.3 section 2/3: "If Dhan and Angel One conflict: DATA_CONFLICT =
    TRUE. Block new entry until resolved." Compares angelone_captured's
    spot against a genuinely independent live source (dhan_chain) for the
    SAME resolved expiry -- becomes possible only now that dhan_chain is a
    real, verified source (2026-09-30); previously there was no second
    live chain source to compare against at all.

    Deliberately an on-demand, read-only spot-check -- NOT called from any
    hot decision path. app.hedging.runner uses only the offline captured
    chain by design (its own docstring: a <=5-min-stale captured chain is
    an acceptable, deliberately conservative tradeoff against the
    complexity of a new live feed dependency); wiring a live Dhan round-
    trip into that path would add latency and a new failure mode to
    something that already works, which is a separate decision from
    exposing this check as a real, available capability."""
    u = str(underlying or "").upper()
    ang = get_chain(u, "AUTO", allow_network=False, provider="ANGELONE")
    if ang is None or not ang.rows or ang.spot is None:
        return {"status": "UNAVAILABLE", "reason": "no angelone_captured chain (or no spot)"}

    from .sources import dhan_chain as _dhan
    try:
        dhan = _dhan.fetch(u, ang.expiry)
    except Exception as e:
        return {"status": "UNAVAILABLE", "reason": f"dhan_chain fetch failed: {type(e).__name__}",
               "angelone_expiry": ang.expiry}
    if dhan is None or not dhan.rows or dhan.spot is None:
        return {"status": "UNAVAILABLE", "reason": "no dhan_chain data (or no spot) for this expiry",
               "angelone_expiry": ang.expiry}

    diff_pct = round(abs(ang.spot - dhan.spot) / ang.spot * 100.0, 4) if ang.spot else None
    conflict = diff_pct is not None and diff_pct > tolerance_pct
    return {
        "status": "CONFLICT" if conflict else "OK",
        "underlying": u, "expiry": ang.expiry,
        "angelone_spot": ang.spot, "angelone_ts": ang.ts,
        "dhan_spot": dhan.spot, "dhan_ts": dhan.ts,
        "diff_pct": diff_pct, "tolerance_pct": tolerance_pct,
    }
