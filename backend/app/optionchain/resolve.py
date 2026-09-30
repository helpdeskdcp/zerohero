"""
Source ordering + failover + Greek-merge.

`get_chain(underlying, expiry="AUTO")`:
  1. try angelone_captured (offline, from the capture DB)   -- PRIMARY
  2. try upstox_open  (keyless HTTP)                         -- SECONDARY
  3. try nse_v3       (keyless HTTP, cookie-primed)          -- TERTIARY
First non-empty chain wins. If the winner lacks Greeks and a later source has
them, Greeks are merged onto the winner's rows by (strike, side).

`allow_network=False` restricts to angelone_captured -> deterministic + offline
(what tests and cron use).
"""
from __future__ import annotations

from datetime import datetime, timezone

from .chain import OptionChain
from .quality import score as _quality
from .sources import angelone_chain, dhan_chain, nse_v3, upstox_open

# dhan_chain is last in the default order deliberately -- verified live
# 2026-09-30 (real 238-strike NIFTY chain, 100% Greek coverage, real
# bid/ask), but brand-new: angelone_captured has run continuously for
# weeks and is the thing that is NOT broken, so it keeps default priority.
# dhan_chain is a real, equally-capable (arguably richer -- one call gets
# bid/ask+OI+previous_oi+volume+IV+all 4 Greeks, vs AngelOne's separate
# quote+greek endpoints) source, reachable today via prefer="dhan_chain"
# for anyone who wants to compare or explicitly prioritise it, and as a
# last-resort fallback if every existing source fails. Promoting it ahead
# of angelone_captured is a real, separate decision -- not made here.
_ORDER = ("angelone_captured", "upstox_open", "nse_v3", "dhan_chain")
_FETCH = {
    "angelone_captured": lambda u, e, kw: angelone_chain.fetch(u, e, db_path=kw.get("db_path"),
                                                               at_ts=kw.get("at_ts")),
    "upstox_open": lambda u, e, kw: upstox_open.fetch(u, e, session=kw.get("session")),
    "nse_v3": lambda u, e, kw: nse_v3.fetch(u, e, session=kw.get("session")),
    "dhan_chain": lambda u, e, kw: dhan_chain.fetch(u, e),
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
              allow_network: bool = True, atm_window: int = 10, **kw) -> OptionChain | None:
    order = ([prefer] + [s for s in _ORDER if s != prefer]) if prefer in _ORDER else list(_ORDER)
    if not allow_network:
        order = [s for s in order if s == "angelone_captured"]

    chain = None
    tried = []
    for name in order:
        try:
            c = _FETCH[name](underlying, expiry, kw)
        except Exception as e:                    # a source must never break the resolver
            tried.append(f"{name}: ERR {type(e).__name__}")
            continue
        tried.append(f"{name}: {'ok(' + str(len(c.rows)) + ')' if c and c.rows else 'empty'}")
        if c and c.rows:
            chain = c
            break
    if chain is None:
        return None

    # ---- Greek merge if the winner has none ----
    if not (chain.capability or {}).get("has_greeks") and allow_network:
        for name in order:
            if name == chain.source.split("+")[0]:
                continue
            try:
                g = _FETCH[name](underlying, chain.expiry, kw)
            except Exception:
                g = None
            if g and (g.capability or {}).get("has_greeks"):
                merged = _merge_greeks(chain, g)
                if merged:
                    chain.source = f"{chain.source}+greeks:{name}"
                    chain.capability["has_greeks"] = True
                    chain.capability["greek_coverage"] = round(merged / (2 * len(chain.rows)), 3)
                    chain.notes.append(f"Greeks merged from {name} ({merged} legs)")
                    chain.compute_atm()
                break

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
    ang = get_chain(u, "AUTO", allow_network=False)
    if ang is None or not ang.rows or ang.spot is None:
        return {"status": "UNAVAILABLE", "reason": "no angelone_captured chain (or no spot)"}

    from .sources import dhan_chain as _dhan
    try:
        dhan = _dhan.fetch(u, ang.expiry)
    except Exception as e:
        return {"status": "UNAVAILABLE", "reason": f"dhan_chain fetch failed: {type(e).__name__}: {e}",
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
