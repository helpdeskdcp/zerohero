"""
TERTIARY source -- NSE `api/option-chain-v3`. OI / change-OI / IV / volume.
NO Greeks. NSE indices only (NIFTY / BANKNIFTY / FINNIFTY / MIDCPNIFTY /
NIFTYNXT50) -- NO SENSEX (that is BSE).

`parse()` is pure; `fetch()` primes NSE's anti-bot cookie (a GET to the
option-chain page) then calls the JSON API on the same session. URL + headers
taken as public facts from prasanna-venkatesh-m/NSE-OptionChain-Dashboard;
no code copied (no licence). The cookie-prime step is our addition -- the
reference proxy omits it and 403s in production.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..chain import OptionChain, StrikeRow, OptionLeg

_HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
}
SUPPORTED = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _iv(v):
    f = _num(v)
    if f is None or f == 0:
        return None
    return f / 100.0 if f > 1.5 else f          # NSE gives IV as a percent number


def _to_ddmmmyyyy(expiry: str) -> str | None:
    for f in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(expiry).upper(), f).strftime("%d-%b-%Y")
        except ValueError:
            continue
    return None


def parse(obj: dict, underlying: str, expiry: str, *, ts: str | None = None) -> OptionChain | None:
    recs = (obj or {}).get("records") or obj or {}
    data = recs.get("data") or (obj or {}).get("data") or []
    if not data:
        return None
    spot = _num(recs.get("underlyingValue") or (obj or {}).get("underlyingValue"))
    want_exp = str(expiry).upper()

    by_k: dict[float, StrikeRow] = {}

    def _leg(node) -> OptionLeg | None:
        if not node:
            return None
        return OptionLeg(
            ltp=_num(node.get("lastPrice")),
            bid=_num(node.get("bidprice") or node.get("bidPrice")),
            ask=_num(node.get("askPrice") or node.get("askprice")),
            bid_qty=_num(node.get("bidQty")), ask_qty=_num(node.get("askQty")),
            oi=_num(node.get("openInterest")),
            oi_change=_num(node.get("changeinOpenInterest")),
            volume=_num(node.get("totalTradedVolume")),
            iv=_iv(node.get("impliedVolatility")))

    for d in data:
        k = _num(d.get("strikePrice"))
        e = str(d.get("expiryDate") or "").upper()
        if k is None:
            continue
        # accept if the row's expiry matches the request (in any format) or the
        # request was AUTO/blank
        if want_exp not in ("", "AUTO", "CURRENT") and e:
            de = None
            for f in ("%d-%b-%Y", "%d%b%Y"):
                try:
                    de = datetime.strptime(e, f).strftime("%d%b%Y").upper()
                    break
                except ValueError:
                    pass
            wn = None
            for f in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d"):
                try:
                    wn = datetime.strptime(want_exp, f).strftime("%d%b%Y").upper()
                    break
                except ValueError:
                    pass
            if de and wn and de != wn:
                continue
        row = by_k.setdefault(k, StrikeRow(strike=k))
        if d.get("CE"):
            row.ce = _leg(d["CE"])
        if d.get("PE"):
            row.pe = _leg(d["PE"])
    if not by_k:
        return None
    n = len(by_k)
    chain = OptionChain(
        underlying=str(underlying).upper(), expiry=want_exp, source="nse_v3",
        ts=ts or datetime.now(timezone.utc).isoformat(), spot=spot,
        rows=list(by_k.values()),
        capability={"has_greeks": False, "greek_coverage": 0.0,
                    "has_iv": any(l and l.iv is not None for r in by_k.values() for l in (r.ce, r.pe)),
                    "has_oi": True, "has_oi_change": True,
                    "spot_source": "nse" if spot is not None else "parity_proxy"},
        notes=["source: NSE option-chain-v3 (no Greeks)"])
    return chain.sort().compute_atm()


def fetch(underlying: str, expiry: str, *, timeout: float = 8.0, session=None) -> OptionChain | None:
    u = str(underlying or "").upper()
    if u not in SUPPORTED:
        return None
    exp = _to_ddmmmyyyy(expiry)
    import requests
    s = session or requests.Session()
    try:
        # prime the anti-bot cookie
        s.get("https://www.nseindia.com/option-chain", headers=_HDRS, timeout=timeout)
        params = {"type": "Indices", "symbol": u}
        if exp:
            params["expiry"] = exp
        r = s.get("https://www.nseindia.com/api/option-chain-v3", params=params,
                  headers=_HDRS, timeout=timeout)
        r.raise_for_status()
        return parse(r.json(), u, expiry if exp else "AUTO")
    except Exception:
        return None
