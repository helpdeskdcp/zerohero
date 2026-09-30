"""
Pure risk/economics math for a same-type vertical credit-spread hedge
(SELL a leg, BUY a further-OTM leg of the same CE/PE type as protection).
No I/O, no look-ahead, nothing here fabricates a number it can't derive
from the two real premiums + strikes + lot_size it's given.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SpreadEconomics:
    net_credit_per_unit: float      # primary_premium - hedge_premium (can be negative -> net debit)
    max_loss_per_lot: float         # strike_width * lot_size - net_credit*lot_size, floored at 0
    max_profit_per_lot: float       # net_credit * lot_size, floored at 0
    strike_width: float
    breakeven: float                # the underlying price where combined P&L crosses zero at expiry
    margin_status: str = "APPROXIMATED_AS_MAX_LOSS"
    margin_note: str = (
        "no broker SPAN margin-calculator API is wired in this codebase -- "
        "a defined-risk vertical spread's real margin requirement is bounded "
        "by its own max loss, so max_loss_per_lot is used as the conservative "
        "capital-at-risk figure. Never presented as an exact broker SPAN number."
    )


def credit_spread_economics(*, primary_premium: float, hedge_premium: float,
                            primary_strike: float, hedge_strike: float,
                            option_type: str, lot_size: int) -> SpreadEconomics | None:
    """`option_type`: "CE" -> hedge_strike must be > primary_strike (further OTM
    upside). "PE" -> hedge_strike must be < primary_strike (further OTM
    downside). Returns None if the hedge strike isn't actually further OTM
    (that's not a valid protective hedge for a short leg -- e.g. a CE hedge
    strike below the sold strike doesn't cap anything, it just adds cost)."""
    ot = str(option_type or "").upper()
    if ot == "CE":
        if hedge_strike <= primary_strike:
            return None
        width = hedge_strike - primary_strike
    elif ot == "PE":
        if hedge_strike >= primary_strike:
            return None
        width = primary_strike - hedge_strike
    else:
        return None

    net_credit = primary_premium - hedge_premium
    max_loss = max(0.0, width * lot_size - net_credit * lot_size)
    max_profit = max(0.0, net_credit * lot_size)
    # Credit spread breakeven: the sold strike offset by the net credit
    # received, in the direction risk accrues -- CE (bear call spread) loses
    # as spot rises past primary_strike, so the credit buys room above it;
    # PE (bull put spread) loses as spot falls, so the credit buys room below.
    breakeven = primary_strike + net_credit if ot == "CE" else primary_strike - net_credit
    return SpreadEconomics(net_credit_per_unit=round(net_credit, 4),
                           max_loss_per_lot=round(max_loss, 2),
                           max_profit_per_lot=round(max_profit, 2),
                           strike_width=width,
                           breakeven=round(breakeven, 2))


def spread_pct(bid: float | None, ask: float | None) -> float | None:
    """(ask-bid)/mid, or None when either side is missing/invalid -- shared
    by both leg validators (selector.py's hedge-leg check and
    primary_selector.py's sold-leg check) so there is exactly one
    definition of "spread", not two that could quietly drift apart."""
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    return round((ask - bid) / mid, 4) if mid > 0 else None


def net_delta_exposure(*, primary_delta: float | None, hedge_delta: float | None,
                       primary_side: str, lot_size: int, lots: int, spot: float | None) -> dict:
    """Delta-weighted directional exposure in rupees, after the hedge.
    `primary_side`: "SELL" (the only side this phase hedges) -- a sold
    option's position delta is the NEGATIVE of its quoted (long-option)
    delta; the bought hedge leg's position delta is its own quoted delta
    as-is. Returns UNAVAILABLE (never a fabricated 0) when either delta or
    spot is missing."""
    if primary_delta is None or hedge_delta is None or spot is None:
        return {"status": "UNAVAILABLE", "net_delta": None, "exposure_rupees": None}
    primary_pos_delta = -primary_delta if str(primary_side).upper() == "SELL" else primary_delta
    net_delta = (primary_pos_delta + hedge_delta) * lot_size * lots
    return {"status": "OK", "net_delta": round(net_delta, 4),
           "exposure_rupees": round(net_delta * spot, 2)}


def risk_reduction_pct(*, unhedged_max_loss: float | None, hedged_max_loss: float) -> dict:
    """A naked short option's theoretical max loss is unbounded -- there is
    no real finite "unhedged" figure to compute a percentage against, so
    that case is reported explicitly (UNBOUNDED_TO_DEFINED), never as a
    fabricated "100%". `unhedged_max_loss` is only a real number when the
    caller has one (e.g. a configured worst-case reference), not invented
    here."""
    if unhedged_max_loss is None:
        return {"status": "UNBOUNDED_TO_DEFINED", "pct": None,
               "note": "naked short has theoretically unbounded max loss; "
                       "the hedge caps it at a defined, finite figure"}
    if unhedged_max_loss <= 0:
        return {"status": "UNAVAILABLE", "pct": None}
    pct = max(0.0, (unhedged_max_loss - hedged_max_loss) / unhedged_max_loss * 100.0)
    return {"status": "OK", "pct": round(pct, 2)}
