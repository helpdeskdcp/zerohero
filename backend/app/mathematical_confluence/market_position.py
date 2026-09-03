"""
Mathematical market position + regime (section 12).

All ratios are causal (only PDH/PDL/PDC + today's open + running day
high/low + current price). No future data.
"""
from __future__ import annotations


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def market_position(*, pdh, pdl, pdc, today_open, day_high, day_low, current_price):
    H, L, C = _f(pdh), _f(pdl), _f(pdc)
    O, DH, DL, P = _f(today_open), _f(day_high), _f(day_low), _f(current_price)
    out = {
        "position_in_prev_day_range": None,
        "position_in_intraday_range": None,
        "open_vs_prev_close": None,
        "open_vs_prev_high": None,
        "open_vs_prev_low": None,
        "open_type": None,
    }
    if None not in (H, L, P) and H > L:
        out["position_in_prev_day_range"] = round((P - L) / (H - L), 4)
    if None not in (DH, DL, P) and DH > DL:
        out["position_in_intraday_range"] = round((P - DL) / (DH - DL), 4)
    if None not in (O, C):
        out["open_vs_prev_close"] = round(O - C, 2)
    if None not in (O, H):
        out["open_vs_prev_high"] = round(O - H, 2)
    if None not in (O, L):
        out["open_vs_prev_low"] = round(O - L, 2)
    # gap classification
    if None not in (O, H, L, C):
        if O > H:
            out["open_type"] = "GAP_UP_ABOVE_PDH"
        elif O < L:
            out["open_type"] = "GAP_DOWN_BELOW_PDL"
        elif O > C:
            out["open_type"] = "OPEN_ABOVE_PDC"
        elif O < C:
            out["open_type"] = "OPEN_BELOW_PDC"
        else:
            out["open_type"] = "FLAT_OPEN"
    return out


_REGIMES = ("BULLISH_EXPANSION", "BEARISH_EXPANSION", "RANGE", "BREAKOUT_ATTEMPT",
            "BREAKDOWN_ATTEMPT", "REVERSAL", "NEUTRAL")


def classify_regime(mp: dict, *, prev_range, day_range, mom_3m=None, near_zone=None,
                    breakout_state=None) -> dict:
    """Rule-based, interpretable. Returns {regime, confidence, reasons}. NOT a
    calibrated probability."""
    reasons = []
    pos_prev = mp.get("position_in_prev_day_range")
    pos_day = mp.get("position_in_intraday_range")
    ot = mp.get("open_type")

    # expansion vs range from how much of the prev-day range today has covered
    expanded = day_range is not None and prev_range and day_range > 1.05 * prev_range

    regime = "NEUTRAL"
    conf = 40
    if breakout_state in ("BREAKOUT_CONFIRMED",):
        regime, conf = "BREAKOUT_ATTEMPT", 65
        reasons.append("confirmed breakout of a confluence zone")
    elif breakout_state in ("BREAKDOWN_CONFIRMED",):
        regime, conf = "BREAKDOWN_ATTEMPT", 65
        reasons.append("confirmed breakdown of a confluence zone")
    elif ot == "GAP_UP_ABOVE_PDH" and (pos_prev or 0) > 0.9:
        regime, conf = "BULLISH_EXPANSION", 60
        reasons.append("gap-up above PDH, price holding upper prev-day range")
    elif ot == "GAP_DOWN_BELOW_PDL" and (pos_prev or 1) < 0.1:
        regime, conf = "BEARISH_EXPANSION", 60
        reasons.append("gap-down below PDL, price in lower prev-day range")
    elif expanded and (pos_prev or 0.5) > 0.7 and (mom_3m or 0) > 0:
        regime, conf = "BULLISH_EXPANSION", 55
        reasons.append("range expansion up with positive momentum")
    elif expanded and (pos_prev or 0.5) < 0.3 and (mom_3m or 0) < 0:
        regime, conf = "BEARISH_EXPANSION", 55
        reasons.append("range expansion down with negative momentum")
    elif not expanded and 0.3 <= (pos_prev or 0.5) <= 0.7:
        regime, conf = "RANGE", 50
        reasons.append("price mid prev-day range, no expansion")
    if near_zone and near_zone.get("evidence_count", 0) >= 3 and abs(mom_3m or 0) < 1e-6:
        reasons.append(f"pinned to a {near_zone['evidence_count']}-evidence zone at {near_zone['center']}")
    return {"regime": regime, "confidence": conf, "reasons": reasons}
