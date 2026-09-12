"""
Read-only HTTP surface for the Microstructure layer.

Same convention as the other subsystem routers: a GET here computes over
already-captured data (h1h7's own shadow-CSV / captured bars via
market_hub), never a write to order execution or broker credentials.
Attaching option-chain context is opt-in (`with_option_context=1`) and,
even then, defaults to no live/network fetch (`live=0`) -- same convention
as optionchain/api.py's own `live` query param.
"""
from __future__ import annotations

from fastapi import APIRouter

from .evaluator import evaluate

router = APIRouter(prefix="/api/microstructure", tags=["microstructure"])


@router.get("/status/{symbol}")
def status(symbol: str, session_date: str, tf: str = "5m",
           with_option_context: int = 0, expiry: str = "AUTO", live: int = 0):
    chain_analytics = None
    if with_option_context:
        try:
            from ..optionchain.analytics import compute_all
            from ..optionchain.resolve import get_chain
            chain = get_chain(symbol.upper(), expiry, allow_network=bool(live))
            chain_analytics = compute_all(chain)
        except Exception:
            chain_analytics = None   # option context is best-effort supplementary evidence only
    return evaluate(symbol, session_date, tf=tf, chain_analytics=chain_analytics)
