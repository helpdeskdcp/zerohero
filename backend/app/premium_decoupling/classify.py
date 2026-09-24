"""Deterministic sign-relationship classifier for spot vs. ATM CE/PE premium
moves over a window. Descriptive only -- a label for what happened, not a
prediction of what happens next. Thresholds below are dead-zones to stop
near-zero noise from flip-flopping the label, not calibrated/backtested
parameters; do not present classification frequency as a validated edge.
"""

SPOT_FLAT_PCT = 0.02     # |spot move| below this %% => treated as FLAT
PREMIUM_FLAT_PCT = 1.0   # |premium move| below this %% => treated as FLAT


def _dir(delta_pct: float | None, flat_thresh: float) -> str:
    if delta_pct is None:
        return "NA"
    if abs(delta_pct) < flat_thresh:
        return "FLAT"
    return "UP" if delta_pct > 0 else "DOWN"


# (spot_dir, ce_dir, pe_dir) -> (label, explanation)
_RULES = {
    ("UP", "UP", "DOWN"): ("NORMAL_BULLISH",
        "index up, CE gaining delta, PE losing delta+theta -- textbook up-move"),
    ("UP", "DOWN", "DOWN"): ("BULLISH_PREMIUM_DRAG",
        "index up but CE premium also falling -- theta/IV decay or call-writing "
        "is overpowering the delta gain; the up-move lacks options-side conviction"),
    ("UP", "DOWN", "UP"): ("BEARISH_DIVERGENCE",
        "index up yet CE falling AND PE rising -- options market pricing the "
        "opposite of the spot move"),
    ("UP", "UP", "UP"): ("VOL_EXPANSION_UP",
        "index up and both CE+PE gaining -- IV expanding alongside the move"),
    ("DOWN", "DOWN", "UP"): ("NORMAL_BEARISH",
        "index down, PE gaining delta, CE losing delta+theta -- textbook down-move"),
    ("DOWN", "UP", "DOWN"): ("BEARISH_DIVERGENCE",
        "index down yet CE rising AND PE falling -- options market pricing the "
        "opposite of the spot move"),
    ("DOWN", "DOWN", "DOWN"): ("BEARISH_PREMIUM_DRAG",
        "index down but PE premium also falling -- theta/IV decay or put-writing "
        "is overpowering the delta gain; the down-move lacks options-side conviction"),
    ("DOWN", "UP", "UP"): ("VOL_EXPANSION_DOWN",
        "index down and both CE+PE gaining -- IV expanding alongside the move"),
    ("FLAT", "DOWN", "DOWN"): ("THETA_BLEED",
        "spot flat, both CE and PE losing premium -- pure time decay, no directional move"),
    ("FLAT", "UP", "UP"): ("VOL_EXPANSION_FLAT",
        "spot flat but both CE and PE gaining premium -- IV rising without a price move"),
    ("FLAT", "UP", "DOWN"): ("SKEW_SHIFT_FLAT",
        "spot flat, CE gaining and PE losing -- one-sided premium shift despite no spot move"),
    ("FLAT", "DOWN", "UP"): ("SKEW_SHIFT_FLAT",
        "spot flat, PE gaining and CE losing -- one-sided premium shift despite no spot move"),
    ("FLAT", "FLAT", "FLAT"): ("FLAT", "no meaningful move on spot or either premium"),
}


def classify(spot_delta_pct: float | None, ce_delta_pct: float | None,
             pe_delta_pct: float | None) -> dict:
    if spot_delta_pct is None or ce_delta_pct is None or pe_delta_pct is None:
        return {"classification": "INSUFFICIENT_DATA",
                "explanation": "one or more of spot/CE/PE had no usable premium data in this window",
                "spot_dir": "NA", "ce_dir": "NA", "pe_dir": "NA"}
    s = _dir(spot_delta_pct, SPOT_FLAT_PCT)
    c = _dir(ce_delta_pct, PREMIUM_FLAT_PCT)
    p = _dir(pe_delta_pct, PREMIUM_FLAT_PCT)
    label, explanation = _RULES.get((s, c, p), (
        "MIXED", f"no named pattern for spot={s} ce={c} pe={p}"))
    return {"classification": label, "explanation": explanation,
            "spot_dir": s, "ce_dir": c, "pe_dir": p}
