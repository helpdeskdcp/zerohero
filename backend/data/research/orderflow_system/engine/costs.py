"""
Real-cost model for this backtest's actual traded instrument.

This backtest simulates trades at the raw NIFTY INDEX price level (the only
level with 5 years of real history -- see README's data-mode note). The
index itself isn't directly tradeable; the closest real, liquid instrument
that tracks it point-for-point is the NIFTY FUTURE. Reusing
app.institutional_edge.costs.estimate_index_option_cost here would be
WRONG -- that formula's STT/turnover math assumes OPTION PREMIUM notional
(tens to a few hundred points per lot), not the index's own level
(tens of thousands per lot); naively feeding index points into it as if
they were a premium would silently produce a nonsense (drastically wrong)
cost. Index OPTIONS and index FUTURES also have genuinely different STT
rates -- conflating them would misstate costs, not just approximate them.

FUTURES rates verified live 2026-09-29 (Zerodha's own published charges
page, https://zerodha.com/charges -- a broker's own current-rate page,
same standard used for the options rate card in institutional_edge/costs.py):
  - STT: 0.05% on SELL side notional only (index/stock futures, undifferentiated
    on that page -- lower than options' 0.15% sell-side rate, a real, separate number)
  - Exchange transaction charge: 0.00183% of notional, both legs (NSE)
  - SEBI turnover fee: Rs 10 / crore (both legs) -- same rate as options
  - Stamp duty: 0.002% (or Rs 200/crore) on BUY side notional only
  - Brokerage: Rs 20 flat per executed order (same convention as the
    options rate card)
  - GST: 18%, applied ONLY to (brokerage + exchange_txn + SEBI fee), never
    to STT/stamp duty (those are themselves taxes) -- identical convention
    to the options rate card.

Status is always "FUTURES_FORMULA_ESTIMATE", never "OK" -- this is a
formula built from published rates, not a real contract note (the same
honesty discipline as institutional_edge/costs.py's own "FORMULA_ESTIMATE"
for index options).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FuturesRateCard:
    brokerage_per_order: float = 20.0
    exchange_txn_pct: float = 0.00183 / 100
    stt_sell_pct: float = 0.05 / 100
    stamp_duty_buy_pct: float = 0.002 / 100
    sebi_turnover_pct: float = 0.0001 / 100
    gst_pct: float = 0.18
    verified_on: str = "2026-09-29"
    source: str = "Zerodha published charges page (zerodha.com/charges), F&O Futures section"


def estimate_index_future_cost(entry_price: float, exit_price: float, lot_size: float, *,
                               lots: int = 1, slippage_points: float = 0.75,
                               rates: FuturesRateCard | None = None) -> dict:
    """entry_price/exit_price: the INDEX FUTURE's own price (points), not an
    option premium. Returns a FUTURES_FORMULA_ESTIMATE breakdown, itemized,
    same shape/spirit as institutional_edge.costs.estimate_index_option_cost."""
    r = rates or FuturesRateCard()
    qty = lot_size * lots
    buy_notional = entry_price * qty
    sell_notional = exit_price * qty

    brokerage = r.brokerage_per_order * 2  # one order each side
    exchange_txn = (buy_notional + sell_notional) * r.exchange_txn_pct
    stt = sell_notional * r.stt_sell_pct
    stamp_duty = buy_notional * r.stamp_duty_buy_pct
    sebi_fee = (buy_notional + sell_notional) * r.sebi_turnover_pct
    gst = (brokerage + exchange_txn + sebi_fee) * r.gst_pct
    slippage_cost = slippage_points * qty

    total_cost = brokerage + exchange_txn + stt + stamp_duty + sebi_fee + gst + slippage_cost
    return {
        "status": "FUTURES_FORMULA_ESTIMATE",
        "brokerage": round(brokerage, 2), "exchange_txn": round(exchange_txn, 2),
        "stt": round(stt, 2), "stamp_duty": round(stamp_duty, 2), "sebi_fee": round(sebi_fee, 2),
        "gst": round(gst, 2), "slippage_cost": round(slippage_cost, 2),
        "total_cost": round(total_cost, 2), "total_cost_points": round(total_cost / qty, 4),
        "note": f"formula estimate from published rates (verified {r.verified_on}, {r.source}), "
               f"not a real contract note",
    }
