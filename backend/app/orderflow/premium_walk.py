"""
Premium-basis re-walk for the order-flow backtest -- pure, read-only.

The smart-money engine (smart_money.py) builds and resolves a setup ENTIRELY
in INDEX points: entry = spike high/low, stop/target = index points away,
outcome walked on the index 5m bars. But an order-flow trade is a LONG ATM
option (BUY setup -> long CE, SELL setup -> long PE), whose premium moves
~delta x dS (delta ~0.5 ATM, falling OTM) minus theta -- measured ~0.4 pts
per index point over 15-45 min holds (see ORDERFLOW_PREMIUM_SLIPPAGE.md).

This module keeps the SIGNAL and its EXIT TRIGGER exactly as the engine
computed them (the index hitting its stop/target is the rule), and only
re-prices the realised P&L on the captured option premium series:

    premium_entry = ATM option LTP as-of the breakout bar
    premium_exit  = ATM option LTP as-of the bar the index resolved on
    premium_points = premium_exit - premium_entry        (long option)

WIN / LOSS / FLAT is then taken from the sign of premium_points, NOT from
the index TARGET_HIT / STOP_HIT label -- so an index target that the option
did not follow (IV crush) correctly shows up as a losing trade.

No writes, no broker calls, no order path. Data-limited: only sessions with
captured option quotes; the caller falls back to index basis otherwise.
"""
from __future__ import annotations

from bisect import bisect_right
from typing import Optional


def _asof(series: list, ts: str):
    """Last (ts, ltp) value at or before `ts`. `series` sorted by ts; ISO
    strings compare lexically (fixed-width, zero-padded, all ...Z)."""
    if not series or not ts:
        return None
    i = bisect_right(series, (ts, float("inf")))
    return series[i - 1][1] if i > 0 else None


def _pick_atm(opt_map: dict, ref_price: float, option_type: str) -> Optional[float]:
    """Nearest captured strike to `ref_price` that has a series for this side."""
    strikes = sorted({k[0] for k in opt_map if k[1] == option_type and opt_map.get(k)})
    if not strikes:
        return None
    return min(strikes, key=lambda s: abs(s - ref_price))


def rewalk_leg(opt_map: dict, *, entry_price: float, side: str,
               entry_ts: Optional[str], exit_ts: Optional[str],
               premium_stop_pct: float = 0.0,
               premium_stop_pts: float = 0.0) -> Optional[dict]:
    """Re-price one resolved leg on the captured option premium.

    opt_map     : market_hub.session_option_quotes() output for the session
    entry_price : the INDEX entry level (used only to pick the ATM strike)
    side        : "BUY" -> long CE, "SELL" -> long PE
    entry_ts    : leg["breakout_bar"]   (index bar the breakout fired on)
    exit_ts     : outcome["resolved_bar"] (index bar target/stop hit on)
    premium_stop_pct : optional hard stop on the OPTION as a FRACTION of the
        entry premium. 0 (default) = none; 0.30 = exit the moment a captured
        tick shows the premium down >=30% from entry.
    premium_stop_pts : optional hard stop on the OPTION in ABSOLUTE premium
        points. 0 (default) = none; 25 = exit when premium is >=25 pts below
        entry. When both are set, whichever is hit FIRST along the path wins.
        A premium stop only ever CUTS the hold short, never extends it.

    Returns a premium-space dict, or None when no option series covers the
    window (caller keeps the index-basis result and flags the fallback).
    """
    if not opt_map or not entry_ts or not exit_ts:
        return None
    option_type = "CE" if str(side).upper() == "BUY" else "PE"
    strike = _pick_atm(opt_map, float(entry_price), option_type)
    if strike is None:
        return None
    series = opt_map.get((strike, option_type)) or []
    p_entry = _asof(series, entry_ts)
    p_exit = _asof(series, exit_ts)
    if p_entry is None or p_exit is None or p_entry <= 0:
        return None

    # premium path between entry and the index exit (inclusive)
    lo = bisect_right(series, (entry_ts, -1.0))
    hi = bisect_right(series, (exit_ts, float("inf")))
    pairs = series[lo:hi]

    # the binding stop level is the TIGHTER (higher) of the % and the abs-pts
    # stops that are actually set
    levels = []
    if premium_stop_pct and premium_stop_pct > 0:
        levels.append(p_entry * (1.0 - min(premium_stop_pct, 0.99)))
    if premium_stop_pts and premium_stop_pts > 0:
        levels.append(p_entry - float(premium_stop_pts))
    exit_reason = "INDEX_TRIGGER"
    stop_level = round(max(levels), 4) if levels else None
    if stop_level is not None:
        for ts, v in pairs:
            if ts > entry_ts and v <= stop_level:
                p_exit = v
                pairs = [p for p in pairs if p[0] <= ts]   # truncate path at the stop
                exit_reason = "PREMIUM_STOP"
                break

    path = [v for _, v in pairs] or [p_entry, p_exit]
    mfe = round(max(path) - p_entry, 4)
    mae = round(min(path) - p_entry, 4)

    pts = round(p_exit - p_entry, 4)
    return {
        "basis": "PREMIUM",
        "strike": strike,
        "option_type": option_type,
        "premium_entry": round(p_entry, 4),
        "premium_exit": round(p_exit, 4),
        "premium_points": pts,
        "premium_mfe": mfe,
        "premium_mae": mae,
        "premium_ticks": len(path),
        "premium_exit_reason": exit_reason,
        "premium_stop_level": stop_level,
        # too few captured ticks in the window -> the entry/exit LTP is likely
        # stale (illiquid strike / frozen quote); the P&L is low-confidence.
        "premium_thin": len(path) <= 2,
    }
