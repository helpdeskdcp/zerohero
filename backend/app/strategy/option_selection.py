"""
Option contract selection -- runs ONLY after CE/PE direction is already
confirmed by the verifier. Deliberately kept separate from directional
logic (spec: "Keep option selection separate from direction detection" /
"Do not mix contract selection logic with directional strategy logic").

This is intentionally a thin, configurable selector, not a duplicate of
app.engines.option_engine.select_option (which scores real chain quality/
liquidity/greeks for the live autoscalp path) or app.liquidity_sweep.
strikes.rank_strikes -- this package has no chain-quality data of its own
to work with by design; it only maps a direction + spot + strike step to
an ATM/ITM/OTM strike. A caller with a real option chain should prefer
option_engine.select_option for the final contract pick.
"""
from __future__ import annotations

from dataclasses import dataclass

# Strike offset (in strike_step units) from ATM. A call's ITM strikes are
# BELOW spot (negative offset); a put's ITM strikes are ABOVE spot
# (positive offset) -- the two are mirror images, spelled out explicitly
# rather than derived from a sign formula to avoid an off-by-sign bug.
_OFFSET = {
    ("CE", "ATM"): 0, ("CE", "ITM"): -1, ("CE", "OTM"): 1,
    ("PE", "ATM"): 0, ("PE", "ITM"): 1, ("PE", "OTM"): -1,
}


@dataclass
class ContractSelection:
    direction: str            # "CE" | "PE"
    moneyness: str            # "ATM" | "ITM" | "OTM"
    strike: float


def select_contract(direction: str, spot: float, *, strike_step: float = 50.0,
                    moneyness: str = "ATM") -> ContractSelection:
    if (direction, moneyness) not in _OFFSET:
        raise ValueError(f"select_contract requires a resolved CE/PE direction and a "
                         f"valid moneyness, got direction={direction!r} moneyness={moneyness!r}")
    atm = round(spot / strike_step) * strike_step
    strike = atm + _OFFSET[(direction, moneyness)] * strike_step
    return ContractSelection(direction=direction, moneyness=moneyness, strike=strike)
