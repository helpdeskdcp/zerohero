"""
OPTION EXECUTION -- SEPARATE downstream advisory layer (spec: keep index signal
and option execution separate; option premium noise must NEVER feed back into
the index signal logic).

Input: the ALREADY-decided index fields (direction + optimal index entry / SL /
targets). Output: a CE/PE + strike + *approximate* premium-space levels using a
single delta assumption. This is advisory only -- it runs no premium simulation,
touches nothing upstream, and is not part of the backtest P&L (which is measured
in index points).
"""
from __future__ import annotations


def map_to_option(index_setup: dict, *, spot: float, strike_step: float = 50.0,
                  moneyness: str = "ATM", assumed_delta: float = 0.5) -> dict:
    d = index_setup["direction"]
    opt_type = "CE" if d == "LONG" else "PE"
    atm = round(spot / strike_step) * strike_step
    # slight ITM bias for directional conviction; OTM for cheaper lotto (config choice)
    shift = {"ATM": 0, "ITM": -1 if d == "LONG" else 1, "OTM": 1 if d == "LONG" else -1}
    strike = atm + shift.get(moneyness, 0) * strike_step

    e = index_setup["entry"]
    sl = index_setup["stop_loss"]
    t1, t2, t3 = index_setup["t1"], index_setup["t2"], index_setup["t3"]
    # premium move ~= |index move| * assumed_delta  (first-order; gamma/vega ignored)
    def prem_delta(target):
        return round(abs(target - e) * assumed_delta, 2)

    return {
        "layer": "OPTION_ADVISORY",
        "method": "FIRST_ORDER_DELTA_APPROX (no premium sim; gamma/vega/theta ignored)",
        "option_type": opt_type, "strike": strike, "moneyness": moneyness,
        "assumed_delta": assumed_delta,
        "index_entry": e, "index_sl": sl, "index_t1": t1, "index_t2": t2, "index_t3": t3,
        "approx_premium_risk": prem_delta(sl),
        "approx_premium_reward_t1": prem_delta(t1),
        "approx_premium_reward_t2": prem_delta(t2),
        "approx_premium_reward_t3": prem_delta(t3),
        "note": ("Advisory. Real strike selection + premium entry/SL/targets require "
                 "the live option chain (bid/ask, IV, greeks) and a separate, "
                 "independently-validated premium engine. Index logic is unaffected."),
    }
