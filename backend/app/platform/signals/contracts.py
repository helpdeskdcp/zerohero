"""Contract candidates: ITM / ATM / OTM ranking over the existing validated ranker.

Reuses app.liquidity_sweep.strikes.rank_strikes (hard filters: spread %, volume, OI, premium, delta band;
normalised score; per-candidate rejection reasons) rather than a new ranking implementation. Missing
Greeks/quotes make a leg INELIGIBLE (with the reason), never "estimated".
"""
from __future__ import annotations

from ...liquidity_sweep.strikes import rank_strikes

BANDS = {"ITM": (0.60, 0.85), "ATM": (0.42, 0.60), "OTM": (0.15, 0.42)}     # |delta| bands (configurable per call)


def legs_from_chain(chain, option_type: str) -> list[dict]:
    """OptionChain rows -> plain leg dicts (strike, ltp, bid, ask, delta, oi, volume, iv, theta, ...)."""
    out = []
    for row in chain.rows:
        leg = row.ce if option_type == "CE" else row.pe
        if leg is None:
            continue
        out.append({"strike": row.strike, "ltp": leg.ltp, "bid": leg.bid, "ask": leg.ask, "bid_qty": leg.bid_qty,
                    "ask_qty": leg.ask_qty, "oi": leg.oi, "oi_change": leg.oi_change, "volume": leg.volume,
                    "iv": leg.iv, "delta": leg.delta, "gamma": leg.gamma, "theta": leg.theta, "vega": leg.vega,
                    "security_id": leg.security_id})
    return out


def select_candidates(legs: list[dict], *, spot: float, option_type: str, bands: dict | None = None,
                      strike_step: float = 50.0, **ranker_kwargs) -> dict:
    """{band: [StrikeCandidate...]} (eligible first, then rejected with reasons) for each band."""
    out = {}
    for band, rng in (bands or BANDS).items():
        itm = band == "ITM"
        # `require_itm=False` + itm_fit weight 0 for ATM/OTM: the ranker's ITM-depth term is meaningless there
        # (liquidity, spread, volume, OI, delta fit and the risk terms still apply unchanged).
        weights = None if itm else {"itm_fit": 0.0}
        out[band] = rank_strikes(legs, spot=spot, option_type=option_type, delta_band=rng, strike_step=strike_step,
                                 require_itm=itm, weights=weights, **ranker_kwargs)
    return out
