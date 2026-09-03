"""
Configurable index universe (spec section 16).

Reuses autoscalp.runner._sym_meta for exchange + strike-step so there is one
source of truth. The scalper's universe is a plain list; the operator sets it
via config / the API `symbols=` param.
"""
from __future__ import annotations

import os

from ..autoscalp.runner import _sym_meta

DEFAULT_UNIVERSE = [s.strip().upper() for s in os.environ.get(
    "SMART_SCALPER_UNIVERSE", "NIFTY,BANKNIFTY,FINNIFTY,SENSEX,MIDCPNIFTY,BANKEX").split(",") if s.strip()]

# index -> (option exchange for the chain call, is a weekly index)
_INDEX_MARKET = {
    "NIFTY": "NSE", "BANKNIFTY": "NSE", "FINNIFTY": "NSE", "MIDCPNIFTY": "NSE",
    "SENSEX": "BSE", "BANKEX": "BSE",
}


def index_meta(symbol: str) -> dict:
    sym = symbol.upper()
    m = dict(_sym_meta(sym))
    m["symbol"] = sym
    m["chain_market"] = _INDEX_MARKET.get(sym, m.get("exchange", "NSE"))
    m["is_index"] = sym in _INDEX_MARKET
    return m


def resolve_universe(symbols: str | list[str] | None = None) -> list[str]:
    if symbols is None:
        return list(DEFAULT_UNIVERSE)
    if isinstance(symbols, str):
        return [s.strip().upper() for s in symbols.split(",") if s.strip()]
    return [str(s).strip().upper() for s in symbols if str(s).strip()]
