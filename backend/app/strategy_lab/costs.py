"""Index-futures / index-options statutory cost model (rates in force from 2026-04-01 per the Union Budget 2026 STT change: futures sell side 0.05%, options sell
premium 0.15%). Other items are the published exchange/regulator schedule as last known and MUST be checked against a real contract note before any money decision.
Pure arithmetic; returns BASIS POINTS OF NOTIONAL so it can be compared with strategy edges measured in bps."""
from __future__ import annotations

FUT = {"stt_sell": 0.0005, "txn_per_side": 0.0000183, "stamp_buy": 0.00002, "sebi_per_side": 0.000001, "gst": 0.18}
OPT = {"stt_sell_premium": 0.0015, "txn_per_side": 0.0003503, "stamp_buy": 0.00003, "sebi_per_side": 0.000001, "gst": 0.18}


def futures_round_trip_bps(notional: float, brokerage_per_order: float = 20.0, slippage_bps: float = 0.3) -> dict:
    stt = FUT["stt_sell"] * notional
    txn = 2 * FUT["txn_per_side"] * notional
    stamp = FUT["stamp_buy"] * notional
    sebi = 2 * FUT["sebi_per_side"] * notional
    brok = 2 * brokerage_per_order
    gst = FUT["gst"] * (brok + txn)
    total = stt + txn + stamp + sebi + brok + gst
    to_bps = 1e4 / notional
    return {"statutory_bps": (stt + txn + stamp + sebi + gst) * to_bps, "stt_bps": stt * to_bps, "brokerage_bps": brok * to_bps,
            "slippage_bps": slippage_bps, "total_bps": total * to_bps + slippage_bps}


def option_buy_round_trip_points(entry_premium: float, exit_premium: float, lot: int, brokerage_per_order: float = 20.0, slippage_pts_per_leg: float = 0.25) -> float:
    """cost in index POINTS per unit for buying an option and selling it later (STT on the sold premium)."""
    brok = 2 * brokerage_per_order / lot
    txn = OPT["txn_per_side"] * (entry_premium + exit_premium)
    return (brok + OPT["stt_sell_premium"] * exit_premium + txn + OPT["sebi_per_side"] * (entry_premium + exit_premium) + OPT["stamp_buy"] * entry_premium
            + OPT["gst"] * (brok + txn) + 2 * slippage_pts_per_leg)
