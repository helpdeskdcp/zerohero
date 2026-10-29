"""ONE valuation of an open position, used by the /live rows (card, header and totals all read the same numbers).

    BUY : gross = (mark - average_entry) x open_quantity x unit        SELL : gross = (average_entry - mark) x open_quantity x unit
    net  = gross - estimated round-trip charges (entry leg + exit leg), deducted ONCE
The mark is the last traded price when it is fresh (what the card shows as LTP); without one, the side that would close the position (long: bid, short: ask), and `mark_basis` says which. Options use basis="bid" (an
illiquid last price is not a mark). `exit_now_*` is the separate, conservative figure: what closing right now would realise (long at the bid, short at the ask). A quote older than STALE_QUOTE_S, or no usable price,
gives NO P&L (stale=True) rather than a number built on an old price. Pure: no I/O, no state, so repeated ticks cannot accumulate."""
from __future__ import annotations

STALE_QUOTE_S = 45.0


def gross_pnl(side: str, qty: float, entry: float, mark: float, unit: float = 1.0) -> float:
    return ((mark - entry) if side != "SELL" else (entry - mark)) * qty * unit


def _px(x):
    try: v = float(x)
    except (TypeError, ValueError): return None
    return v if v > 0 else None


def open_valuation(*, side: str, qty: float, entry: float, ltp, bid, ask, age_s, charges_fn, unit: float = 1.0, basis: str = "ltp") -> dict:
    """charges_fn(entry, mark, qty) -> estimated round-trip charges in rupees for that open quantity."""
    side = "SELL" if side == "SELL" else "BUY"; ltp, bid, ask = _px(ltp), _px(bid), _px(ask)
    stale = age_s is not None and age_s > STALE_QUOTE_S
    out = {"mark_price": None, "mark_basis": None, "gross_pnl_rs": None, "est_charges_rs": None, "net_pnl_rs": None, "exit_now_price": None, "exit_now_gross_rs": None, "exit_now_net_rs": None, "stale": bool(stale)}
    close_px = bid if side == "BUY" else ask                                                     # the side that would actually close the position
    mark, mb = (ltp, "ltp") if (basis == "ltp" and ltp) else ((close_px, "bid" if side == "BUY" else "ask") if close_px else (None, None))
    if stale or not mark or not entry or qty <= 0: return out
    g = gross_pnl(side, qty, entry, mark, unit); c = float(charges_fn(entry, mark, qty) or 0.0)
    out.update(mark_price=mark, mark_basis=mb, gross_pnl_rs=round(g, 2), est_charges_rs=round(c, 2), net_pnl_rs=round(g - c, 2))
    if close_px:
        eg = gross_pnl(side, qty, entry, close_px, unit); ec = float(charges_fn(entry, close_px, qty) or 0.0)
        out.update(exit_now_price=close_px, exit_now_gross_rs=round(eg, 2), exit_now_net_rs=round(eg - ec, 2))
    return out
