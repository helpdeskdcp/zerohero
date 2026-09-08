"""
Pressure DYNAMICS over multiple completed candles.  Strictly causal: every
function takes a list of already-closed bars/scores and never peeks forward.

For a window of net-pressure values p[0..n-1] (oldest..newest) it produces:
  weighted_net     recency-weighted mean (half-life decay)
  slope            least-squares slope of p vs index (per bar)
  acceleration     mean 2nd difference (slope of the slope)
  persistence      fraction of bars whose sign matches the window's net sign
  reversal         +1/-1/0  -- newest bars flipped sign vs the window body
  divergence       +1/-1/0  -- price made a new extreme, pressure did NOT
"""
from __future__ import annotations

from .config import merged


def _recency_weights(n: int, halflife: float) -> list[float]:
    # age 0 = newest
    w = [0.5 ** (age / max(1e-6, halflife)) for age in range(n - 1, -1, -1)]
    s = sum(w) or 1.0
    return [x / s for x in w]


def _slope(ys: list[float]) -> float:
    n = len(ys)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = (n - 1) / 2.0
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs) or 1e-9
    return num / den


def dynamics(net_series: list[float], *, cfg: dict | None = None,
            price_extreme: int = 0, pressure_extreme: int = 0) -> dict:
    """net_series oldest..newest (already causal). price_extreme/pressure_extreme:
    +1 if the newest bar set a new window HIGH, -1 for new LOW, 0 otherwise --
    supplied by the caller for the divergence test."""
    c = cfg or merged()
    n = len(net_series)
    if n == 0:
        return {"weighted_net": 0.0, "slope": 0.0, "acceleration": 0.0,
                "persistence": 0.0, "reversal": 0, "divergence": 0, "n": 0}

    wts = _recency_weights(n, c["recency_halflife"])
    wnet = sum(w * p for w, p in zip(wts, net_series))

    sw = min(c["slope_window"], n)
    slope = _slope(net_series[-sw:])
    aw = min(c["accel_window"] + 1, n)
    if aw >= 3:
        seg = net_series[-aw:]
        second_diffs = [seg[i] - 2 * seg[i - 1] + seg[i - 2] for i in range(2, len(seg))]
        accel = sum(second_diffs) / len(second_diffs) if second_diffs else 0.0
    else:
        accel = 0.0

    body_sign = 1 if wnet > 0 else (-1 if wnet < 0 else 0)
    same = sum(1 for p in net_series if (p > 0) == (body_sign > 0) and body_sign != 0)
    persistence = same / n if body_sign else 0.0

    # reversal: the last ceil(n/3) bars vs the window body sign
    tail = net_series[-max(1, n // 3):]
    tail_sign = 1 if sum(tail) > 0 else (-1 if sum(tail) < 0 else 0)
    reversal = -body_sign if (tail_sign != 0 and tail_sign != body_sign) else 0

    # divergence: price new extreme but pressure not confirming that direction
    divergence = 0
    if price_extreme == 1 and pressure_extreme != 1 and slope <= 0:
        divergence = -1                     # new price high, pressure fading -> bearish divergence
    elif price_extreme == -1 and pressure_extreme != -1 and slope >= 0:
        divergence = 1                      # new price low, pressure rising -> bullish divergence

    return {
        "weighted_net": round(wnet, 3),
        "slope": round(slope, 4),
        "acceleration": round(accel, 4),
        "persistence": round(persistence, 3),
        "reversal": reversal,
        "divergence": divergence,
        "n": n,
    }


def multi_window(net_by_bar: list[float], lookbacks: list[int] | None = None,
                 cfg: dict | None = None, price_series: list[float] | None = None,
                 pressure_series: list[float] | None = None) -> dict:
    """Run `dynamics` for each lookback ending at the newest bar. net_by_bar is
    oldest..newest. Returns {lb: {..dynamics..}} plus a fused view."""
    c = cfg or merged()
    lbs = lookbacks or c["lookbacks"]
    out: dict = {}
    for lb in lbs:
        seg = net_by_bar[-lb:]
        pe = ppe = 0
        if price_series and pressure_series and len(price_series) >= lb:
            pw = price_series[-lb:]
            prw = pressure_series[-lb:]
            if pw[-1] >= max(pw):
                pe = 1
            elif pw[-1] <= min(pw):
                pe = -1
            if prw[-1] >= max(prw):
                ppe = 1
            elif prw[-1] <= min(prw):
                ppe = -1
        out[lb] = dynamics(seg, cfg=c, price_extreme=pe, pressure_extreme=ppe)
    # fused: recency-weighted across lookbacks (shorter windows weighted a touch
    # more for responsiveness, but all contribute)
    lw = {1: 0.30, 3: 0.25, 5: 0.20, 10: 0.15, 15: 0.10}
    tot = sum(lw.get(lb, 1.0 / len(lbs)) for lb in lbs) or 1.0
    fused_net = sum(lw.get(lb, 1.0 / len(lbs)) * out[lb]["weighted_net"] for lb in lbs) / tot
    fused_slope = sum(lw.get(lb, 1.0 / len(lbs)) * out[lb]["slope"] for lb in lbs) / tot
    out["fused"] = {"weighted_net": round(fused_net, 3), "slope": round(fused_slope, 4),
                    "persistence": round(sum(out[lb]["persistence"] for lb in lbs) / len(lbs), 3),
                    "any_reversal": int(any(out[lb]["reversal"] for lb in lbs)),
                    "any_divergence": int(any(out[lb]["divergence"] for lb in lbs))}
    return out
