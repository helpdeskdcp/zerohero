"""
Dhan v2 Option Chain API -- verified live 2026-09-30 against real API
responses (MCP 3.3 priority: "verify Dhan option-chain capability... never
claim Dhan primary unless verified end-to-end").

One call (`POST /v2/optionchain`) returns the COMPLETE chain (both CE+PE,
every strike) for one underlying+expiry, including real top-of-book bid/
ask + quantity, OI + previous_oi (so OI-change is a real subtraction, not
materialised against a session baseline the way angelone_chain.py has to),
volume + previous_volume, and all four Greeks + IV -- more complete in a
single call than AngelOne's multi-endpoint (quote + optionGreek) dance
this codebase otherwise needs. `data.last_price` at the top level is the
underlying spot, for free.

Auth: app.dhan_client.get_access_token() (the same stored App-OAuth token
app/connectors/dhan_historical.py already uses) + DHAN_CLIENT_ID, as the
access-token/client-id headers Dhan's v2 API requires.

Scope, deliberately: NSE/BSE INDEX underlyings only (NIFTY/BANKNIFTY/
FINNIFTY/MIDCPNIFTY/SENSEX/BANKEX) -- their security_id is a fixed index
scrip. MCX commodity option chains resolve against the front-month
FUTURE's security_id, which rolls monthly and needs its own resolution
(app.connectors.dhan_instruments.resolve_front_month_future already does
this for the orderflow WS feed) -- not wired here yet, a real follow-up,
not attempted in this pass.

Wired as get_chain()'s PRIMARY source for live (allow_network=True) reads
as of 2026-09-30 (see resolve.py's own module docstring) -- an
expired/invalid Dhan session fails closed (returns None) and falls
through to angelone_captured exactly as before, never breaking anything.
NOT wired into histcap's periodic background CAPTURE (the continuous,
offline-served path hedging/allow_network=False callers use) -- that is a
separate operational decision this file does not make.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

from ..chain import OptionChain, OptionLeg, StrikeRow

_BASE = "https://api.dhan.co/v2"

# underlying -> (security_id, exchange segment for UnderlyingSeg). Index
# scrips only -- verified against the real Dhan instrument master
# (app.connectors.dhan_instruments), not guessed:
#   NIFTY=13, BANKNIFTY=25, FINNIFTY=27, MIDCPNIFTY=442 (all NSE)
#   SENSEX=51, BANKEX=69 (both BSE) -- UnderlyingSeg="IDX_I" works for
#   either exchange (verified live: SENSEX/51 returned a real expiry list
#   under IDX_I exactly like the NSE names do).
_UNDERLYING_SCRIP = {
    "NIFTY": 13, "BANKNIFTY": 25, "FINNIFTY": 27, "MIDCPNIFTY": 442,
    "SENSEX": 51, "BANKEX": 69,
}
SUPPORTED = set(_UNDERLYING_SCRIP)


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _iv(v):
    """Dhan sends implied_volatility as a percent (12.25 = 12.25%) --
    normalise to this codebase's decimal convention (0.1225), same
    convention sources/upstox_open.py's own _iv() already uses."""
    f = _num(v)
    if f is None:
        return None
    return f / 100.0 if f > 1.5 else f


def _to_iso_date(expiry: str) -> str | None:
    """canonical '06OCT2026' (or already-ISO 'YYYY-MM-DD') -> 'YYYY-MM-DD'."""
    s = str(expiry or "").strip()
    for f in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s.upper(), f).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _to_canonical_expiry(iso_date: str) -> str:
    """'2026-10-06' -> '06OCT2026', this codebase's own convention (see
    app.optionchain.chain / app.instruments)."""
    return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d%b%Y").upper()


def _headers() -> dict | None:
    from ... import dhan_client
    token = dhan_client.get_access_token()
    client_id = (os.environ.get("DHAN_CLIENT_ID") or "").strip()
    if not token or not client_id:
        return None
    return {"Content-Type": "application/json", "access-token": token, "client-id": client_id}


def expiry_list(underlying: str, *, timeout: float = 10.0, session=None) -> list[str] | None:
    """Real, live expiry dates for `underlying` ('YYYY-MM-DD' as Dhan sends
    them). None on any auth/network/shape failure -- never a fabricated list."""
    u = str(underlying or "").upper()
    scrip = _UNDERLYING_SCRIP.get(u)
    if scrip is None:
        return None
    headers = _headers()
    if headers is None:
        return None
    s = session or requests.Session()
    try:
        r = s.post(f"{_BASE}/optionchain/expirylist",
                  json={"UnderlyingScrip": scrip, "UnderlyingSeg": "IDX_I"},
                  headers=headers, timeout=timeout)
        if r.status_code != 200:
            return None
        body = r.json()
        if body.get("status") != "success":
            return None
        data = body.get("data")
        return list(data) if isinstance(data, list) else None
    except Exception:
        return None


def parse(obj: dict, underlying: str, expiry: str, *, ts: str | None = None) -> OptionChain | None:
    if not isinstance(obj, dict) or obj.get("status") != "success":
        return None
    data = obj.get("data") or {}
    oc = data.get("oc") or {}
    if not oc:
        return None
    spot = _num(data.get("last_price"))

    def _leg(node: dict | None) -> OptionLeg | None:
        if not node:
            return None
        oi = _num(node.get("oi"))
        prev_oi = _num(node.get("previous_oi"))
        greeks = node.get("greeks") or {}
        return OptionLeg(
            ltp=_num(node.get("last_price")),
            bid=_num(node.get("top_bid_price")), ask=_num(node.get("top_ask_price")),
            bid_qty=_num(node.get("top_bid_quantity")), ask_qty=_num(node.get("top_ask_quantity")),
            oi=oi, oi_change=(oi - prev_oi) if (oi is not None and prev_oi is not None) else None,
            volume=_num(node.get("volume")),
            iv=_iv(node.get("implied_volatility")),
            delta=_num(greeks.get("delta")), gamma=_num(greeks.get("gamma")),
            theta=_num(greeks.get("theta")), vega=_num(greeks.get("vega")),
            security_id=(str(node["security_id"]) if node.get("security_id") is not None else None))

    rows = []
    for k, node in oc.items():
        strike = _num(k)
        if strike is None:
            continue
        rows.append(StrikeRow(strike=strike, ce=_leg(node.get("ce")), pe=_leg(node.get("pe"))))
    if not rows:
        return None
    n = len(rows)
    n_greek = sum(1 for r in rows for leg in (r.ce, r.pe) if leg and leg.delta is not None)
    ch = OptionChain(
        underlying=str(underlying).upper(), expiry=str(expiry).upper(),
        ts=ts or datetime.now(timezone.utc).isoformat(), source="dhan_chain", spot=spot,
        rows=rows,
        capability={"has_greeks": n_greek > 0, "greek_coverage": round(n_greek / (2 * n), 3) if n else 0.0,
                    "has_iv": any(leg and leg.iv is not None for r in rows for leg in (r.ce, r.pe)),
                    "has_oi": True, "has_oi_change": True, "spot_source": "dhan" if spot is not None else None},
        notes=["source: Dhan v2 /optionchain (verified live 2026-09-30) -- "
               "real top-of-book bid/ask, OI, previous_oi, volume, IV, Greeks in one call"])
    return ch.sort().compute_atm().with_expiry_ctx()


def fetch(underlying: str, expiry: str, *, timeout: float = 15.0, session=None) -> OptionChain | None:
    """`expiry`: this codebase's canonical 'DDMMMYYYY' (e.g. '06OCT2026') or
    an already-ISO date. None on any auth/network/shape failure -- a
    source must never fabricate a chain, matching every other source
    here (angelone_chain/upstox_open/nse_v3)."""
    u = str(underlying or "").upper()
    if u not in _UNDERLYING_SCRIP:
        return None
    iso = _to_iso_date(expiry)
    if not iso:
        return None
    headers = _headers()
    if headers is None:
        return None
    s = session or requests.Session()
    try:
        r = s.post(f"{_BASE}/optionchain",
                  json={"UnderlyingScrip": _UNDERLYING_SCRIP[u], "UnderlyingSeg": "IDX_I", "Expiry": iso},
                  headers=headers, timeout=timeout)
        if r.status_code != 200:
            return None
        return parse(r.json(), u, _to_canonical_expiry(iso))
    except Exception:
        return None
