"""
SECONDARY source -- the KEYLESS Upstox `option-analytics-tool/open/v1`
endpoint. Per-strike CE/PE with PRE-COMPUTED Greeks + IV. NIFTY / BANKNIFTY.

`parse()` is pure (takes the decoded JSON); `fetch()` does the one HTTP GET.
Endpoint URL + JSON field names taken as public facts from
PyStatIQ-Lab/OptionsChain-Analysis-dashboard; no code copied (that repo has no
licence). No API key -- the `/open/` path is public.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..chain import OptionChain, StrikeRow, OptionLeg

_BASE = "https://service.upstox.com/option-analytics-tool/open/v1"
_ASSET_KEY = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT",
}
SUPPORTED = set(_ASSET_KEY)


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _iv(v):
    """normalise IV to a decimal (0.11), accepting a percent (11.0) too."""
    f = _num(v)
    if f is None:
        return None
    return f / 100.0 if f > 1.5 else f


def _to_ddmmm(expiry: str) -> str | None:
    """canonical '15SEP2026' -> Upstox wants 'YYYY-MM-DD' or 'DD-MM-YYYY'
    depending on version; we emit ISO 'YYYY-MM-DD'."""
    for f in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(expiry).upper(), f).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse(obj: dict, underlying: str, expiry: str, *, ts: str | None = None) -> OptionChain | None:
    d = (obj or {}).get("data") or obj or {}
    scd = d.get("strategyChainData") or d.get("optionChainData") or d
    smap = scd.get("strikeMap") or scd.get("strikeData") or {}
    if not smap:
        return None
    spot = _num(scd.get("spotPrice") or scd.get("lastPrice") or d.get("spotPrice")
                or d.get("lastPrice"))

    def _leg(node) -> OptionLeg | None:
        if not node:
            return None
        md = node.get("marketData") or {}
        an = node.get("analytics") or {}
        oi = _num(md.get("oi"))
        prev = _num(md.get("prevOi"))
        return OptionLeg(
            ltp=_num(md.get("ltp")), bid=_num(md.get("bidPrice") or md.get("bid")),
            ask=_num(md.get("askPrice") or md.get("ask")),
            bid_qty=_num(md.get("bidQty")), ask_qty=_num(md.get("askQty")),
            oi=oi, oi_change=(oi - prev) if (oi is not None and prev is not None) else None,
            volume=_num(md.get("volume")),
            iv=_iv(an.get("iv")), delta=_num(an.get("delta")), gamma=_num(an.get("gamma")),
            theta=_num(an.get("theta")), vega=_num(an.get("vega")))

    rows = []
    for k, node in smap.items():
        strike = _num(k) if not isinstance(k, dict) else _num(node.get("strikePrice"))
        if strike is None:
            strike = _num((node or {}).get("strikePrice"))
        if strike is None:
            continue
        rows.append(StrikeRow(
            strike=strike,
            ce=_leg(node.get("callOptionData") or node.get("CE") or node.get("ce")),
            pe=_leg(node.get("putOptionData") or node.get("PE") or node.get("pe"))))
    if not rows:
        return None
    n = len(rows)
    n_greek = sum(1 for r in rows for l in (r.ce, r.pe) if l and l.delta is not None)
    chain = OptionChain(
        underlying=str(underlying).upper(), expiry=str(expiry).upper(),
        ts=ts or datetime.now(timezone.utc).isoformat(), source="upstox_open", spot=spot,
        rows=rows,
        capability={"has_greeks": n_greek > 0, "greek_coverage": round(n_greek / (2 * n), 3),
                    "has_iv": any(l and l.iv is not None for r in rows for l in (r.ce, r.pe)),
                    "has_oi": True, "has_oi_change": True,
                    "spot_source": "upstox" if spot is not None else "parity_proxy"},
        notes=["source: Upstox option-analytics open/v1 (keyless)"])
    return chain.sort().compute_atm().with_expiry_ctx()


def fetch(underlying: str, expiry: str, *, timeout: float = 6.0,
          session=None) -> OptionChain | None:
    u = str(underlying or "").upper()
    if u not in _ASSET_KEY:
        return None
    iso = _to_ddmmm(expiry)
    if not iso:
        return None
    import requests
    s = session or requests.Session()
    try:
        r = s.get(f"{_BASE}/strategy-chains",
                  params={"assetKey": _ASSET_KEY[u], "strategyChainType": "PC_CHAIN",
                          "expiry": iso},
                  headers={"accept": "application/json"}, timeout=timeout)
        r.raise_for_status()
        return parse(r.json(), u, expiry)
    except Exception:
        return None
