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
from .sources import angelone_chain, upstox_open, nse_v3

_ORDER = ("angelone_captured", "upstox_open", "nse_v3")
_FETCH = {
    "angelone_captured": lambda u, e, kw: angelone_chain.fetch(u, e, db_path=kw.get("db_path"),
                                                               at_ts=kw.get("at_ts")),
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
