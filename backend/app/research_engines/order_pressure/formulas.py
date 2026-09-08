"""
Candle geometry + buyer/seller/net pressure.  Pure functions, zero-range safe,
scores in [0, 100].  Config-driven (config.merged()).

Enhancements over the base spec (operator-approved 2026-09-08), each flagged
`# ENH`:
  * ENH volume weighting  -- when a bar carries real volume (options), the
    pressure magnitude is scaled by the bar's volume relative to its recent
    average, so a high-participation push counts more than a thin one.
  * ENH trap term         -- a large opposing wick that engulfs the body after a
    directional move is scored as failed-push (adds to the *opposite* side).
"""
from __future__ import annotations

from .config import merged


def candle_geometry(bar: dict, cfg: dict | None = None) -> dict:
    c = cfg or merged()
    eps = c["eps"]
    o, h, l, cl = bar["o"], bar["h"], bar["l"], bar["c"]
    rng = h - l
    if rng <= eps:                                   # doji / zero-range: fully neutral
        return {"range": 0.0, "body": 0.0, "upper_wick": 0.0, "lower_wick": 0.0,
                "body_ratio": 0.0, "upper_wick_ratio": 0.0, "lower_wick_ratio": 0.0,
                "close_location": 0.5, "green": False, "red": False, "zero_range": True}
    body = abs(cl - o)
    upper = h - max(o, cl)
    lower = min(o, cl) - l
    return {
        "range": rng, "body": body, "upper_wick": upper, "lower_wick": lower,
        "body_ratio": body / rng,
        "upper_wick_ratio": upper / rng,
        "lower_wick_ratio": lower / rng,
        "close_location": (cl - l) / rng,
        "green": cl > o, "red": cl < o, "zero_range": False,
    }


def _vol_weight(bar: dict, recent_avg_vol: float | None, cfg: dict) -> float:
    """ENH: 1.0 when no volume info; else clamp(vol / recent_avg, 0.5, 1.6)."""
    v = bar.get("v_delta") if bar.get("v_delta") is not None else bar.get("v")
    if not v or not recent_avg_vol or recent_avg_vol <= cfg["eps"]:
        return 1.0
    r = v / recent_avg_vol
    return max(0.5, min(1.6, r))


def pressure(bar: dict, *, recent_avg_vol: float | None = None, prior_dir: int = 0,
            cfg: dict | None = None) -> dict:
    """buyer / seller / net pressure for ONE bar, 0-100 (net in -100..100).

    prior_dir: sign of the preceding bar's body (+1 green / -1 red / 0) -- used
    only by the ENH trap term."""
    c = cfg or merged()
    g = candle_geometry(bar, c)
    if g["zero_range"]:
        z = c["zero_range_score"]
        return {"buyer": z, "seller": z, "net": 0.0, **{f"geo_{k}": v for k, v in g.items()}}

    w = c["pressure_weights"]
    up = g["green"]
    dn = g["red"]
    dir_body = g["body_ratio"] * (1.0 if up else (-1.0 if dn else 0.0))  # signed

    # buyer components (all 0..1)
    b_body = max(0.0, dir_body)
    b_wick = g["lower_wick_ratio"]                    # long lower wick = buyers defended
    b_loc = g["close_location"]
    # seller components
    s_body = max(0.0, -dir_body)
    s_wick = g["upper_wick_ratio"]
    s_loc = 1.0 - g["close_location"]

    buyer = w["body"] * b_body + w["wick_rejection"] * b_wick + w["close_location"] * b_loc
    seller = w["body"] * s_body + w["wick_rejection"] * s_wick + w["close_location"] * s_loc

    # ENH trap: a directional bar that closes with a dominant opposing wick and
    # gives back most of its body -> failed push; credit the opposite side.
    if prior_dir > 0 and g["upper_wick_ratio"] > 0.45 and g["body_ratio"] < 0.35:
        seller += 0.15
    elif prior_dir < 0 and g["lower_wick_ratio"] > 0.45 and g["body_ratio"] < 0.35:
        buyer += 0.15

    vw = _vol_weight(bar, recent_avg_vol, c)          # ENH
    # centre at 50, scale the deviation by the volume weight, clamp
    buyer_s = max(0.0, min(100.0, 50.0 + (buyer - 0.0) * 100.0 * 0.5 * vw))
    seller_s = max(0.0, min(100.0, 50.0 + (seller - 0.0) * 100.0 * 0.5 * vw))
    # normalise so they oppose: rebalance around their mean
    m = (buyer_s + seller_s) / 2.0
    buyer_s = max(0.0, min(100.0, 50.0 + (buyer_s - m)))
    seller_s = max(0.0, min(100.0, 50.0 + (seller_s - m)))
    return {
        "buyer": round(buyer_s, 3), "seller": round(seller_s, 3),
        "net": round(buyer_s - seller_s, 3),
        "vol_weight": round(vw, 3),
        **{f"geo_{k}": (round(v, 4) if isinstance(v, float) else v) for k, v in g.items()},
    }


def atr(bars: list[dict], window: int, cfg: dict | None = None) -> float | None:
    """Wilder-ish ATR over the last `window` CLOSED bars (causal)."""
    c = cfg or merged()
    if len(bars) < 2:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    w = min(window, len(trs))
    if w <= 0:
        return None
    return sum(trs[-w:]) / w or c["eps"]
