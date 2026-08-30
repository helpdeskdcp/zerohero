"""
Dynamic Support / Resistance engine (spec-1).

Pure & deterministic. Produces ATR-scaled S/R *zones* (not bare points) and a
0-100 strength score for each, from a weighted confluence of:

  previous-day H/L/C, classic floor pivots, intraday + higher-timeframe swing
  pivots, session HOD/LOD, round numbers, VWAP, and (index mode) option-chain
  OI walls + fresh OI writing.

`mode="index"`  -> full model (uses prev-day levels, pivots, OI walls).
`mode="option"` -> premium-price-action only (swings / session / VWAP / round);
                   OI-wall weight is redistributed to the structural factors.

Weights are seed values (config-overridable). They are exposed per-zone in
`components` so P5 calibration can re-fit them against realised outcomes; the
strategy math does not change when they are re-tuned.
"""
from __future__ import annotations

from statistics import mean
from typing import Optional

from .signal_engine import _atr, _vwap

MODEL_VERSION = "sr-engine-v1"

# structural factor -> seed weight (index mode). Sum ~= 1.0.
_W = {
    "confluence": 0.24,
    "touch_quality": 0.20,
    "rejection_count": 0.16,
    "recency": 0.10,
    "htf_agree": 0.10,
    "oi_backing": 0.12,
    "oi_change_backing": 0.05,
    "vwap_prox": 0.03,
}
_FAMILY = {  # candidate source -> family (for confluence counting)
    "prevday_high": "prevday", "prevday_low": "prevday", "prevday_close": "prevday",
    "pivot_P": "pivot", "pivot_R1": "pivot", "pivot_R2": "pivot", "pivot_R3": "pivot",
    "pivot_S1": "pivot", "pivot_S2": "pivot", "pivot_S3": "pivot",
    "swing_high": "swing", "swing_low": "swing",
    "htf_swing_high": "htf", "htf_swing_low": "htf",
    "session_high": "session", "session_low": "session",
    "round": "round", "vwap": "vwap",
    "oi_wall_ce": "oi", "oi_wall_pe": "oi", "oi_write_ce": "oi", "oi_write_pe": "oi",
}


def _num(x):
    try:
        f = float(x)
        return f if f == f and f not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _bars(b):
    """Return (highs, lows, closes, vols, n) from [{o,h,l,c,v}|[t,o,h,l,c,v]]."""
    H, L, C, V = [], [], [], []
    for k in b or []:
        if isinstance(k, (list, tuple)):
            h, l, c = _num(k[2]), _num(k[3]), _num(k[4])
            v = _num(k[5]) if len(k) > 5 else 0.0
        else:
            h, l, c = _num(k.get("h")), _num(k.get("l")), _num(k.get("c"))
            v = _num(k.get("v")) or 0.0
        if None in (h, l, c):
            continue
        H.append(h); L.append(l); C.append(c); V.append(v or 0.0)
    return H, L, C, V, len(C)


def _swings(highs, lows, left=2, right=2, lookback=80):
    """Fractal swing highs/lows over the last `lookback` bars.
    Returns [(idx, price, 'H'|'L')]."""
    n = len(highs)
    lo = max(left, n - lookback)
    out = []
    for i in range(lo, n - right):
        wl, wr = range(i - left, i), range(i + 1, i + right + 1)
        if all(highs[i] >= highs[j] for j in wl) and all(highs[i] > highs[j] for j in wr):
            out.append((i, highs[i], "H"))
        if all(lows[i] <= lows[j] for j in wl) and all(lows[i] < lows[j] for j in wr):
            out.append((i, lows[i], "L"))
    return out


def _pick_tf(bars_by_tf, prefer=("5m", "3m", "15m", "1m")):
    for tf in prefer:
        if len(bars_by_tf.get(tf) or []) >= 20:
            return tf, bars_by_tf[tf]
    # longest available
    tf = max(bars_by_tf or {"1m": []}, key=lambda k: len(bars_by_tf.get(k) or []))
    return tf, bars_by_tf.get(tf) or []


def _candidates(highs, lows, closes, vols, atr, vwap, *, mode, chain, prev_day,
                round_step):
    price = closes[-1]
    cand: list[tuple[float, str, float]] = []   # (level, source, base_weight)

    if mode == "index" and prev_day:
        h, l, c = _num(prev_day.get("high")), _num(prev_day.get("low")), _num(prev_day.get("close"))
        if None not in (h, l, c):
            cand += [(h, "prevday_high", 1.0), (l, "prevday_low", 1.0), (c, "prevday_close", 0.8)]
            P = (h + l + c) / 3
            rng = h - l
            for name, lvl in (("P", P), ("R1", 2 * P - l), ("S1", 2 * P - h),
                              ("R2", P + rng), ("S2", P - rng),
                              ("R3", h + 2 * (P - l)), ("S3", l - 2 * (h - P))):
                cand.append((lvl, f"pivot_{name}", 0.7))

    for i, lvl, k in _swings(highs, lows):
        age = (len(closes) - 1 - i) / max(1, len(closes))
        cand.append((lvl, "swing_high" if k == "H" else "swing_low", 0.9 * (1 - 0.5 * age)))

    if atr and vwap is not None:
        cand.append((vwap, "vwap", 0.6))
    cand.append((max(highs), "session_high", 0.8))
    cand.append((min(lows), "session_low", 0.8))

    if round_step:
        base = round(price / round_step) * round_step
        for d in (-2, -1, 0, 1, 2):
            cand.append((base + d * round_step, "round", 0.35))

    if mode == "index" and chain:
        tot_ce = sum(_num((r.get("ce") or {}).get("oi")) or 0 for r in chain) or 1.0
        tot_pe = sum(_num((r.get("pe") or {}).get("oi")) or 0 for r in chain) or 1.0
        ce_wall = max(chain, key=lambda r: _num((r.get("ce") or {}).get("oi")) or 0)
        pe_wall = max(chain, key=lambda r: _num((r.get("pe") or {}).get("oi")) or 0)
        cand.append((_num(ce_wall.get("strike")),
                     "oi_wall_ce", 0.5 + 0.8 * ((_num((ce_wall.get("ce") or {}).get("oi")) or 0) / tot_ce)))
        cand.append((_num(pe_wall.get("strike")),
                     "oi_wall_pe", 0.5 + 0.8 * ((_num((pe_wall.get("pe") or {}).get("oi")) or 0) / tot_pe)))
        ce_write = max(chain, key=lambda r: _num((r.get("ce") or {}).get("oi_chg")) or 0)
        pe_write = max(chain, key=lambda r: _num((r.get("pe") or {}).get("oi_chg")) or 0)
        if (_num((ce_write.get("ce") or {}).get("oi_chg")) or 0) > 0:
            cand.append((_num(ce_write.get("strike")), "oi_write_ce", 0.6))
        if (_num((pe_write.get("pe") or {}).get("oi_chg")) or 0) > 0:
            cand.append((_num(pe_write.get("strike")), "oi_write_pe", 0.6))

    return [(lvl, src, w) for lvl, src, w in cand if _num(lvl) is not None]


def _cluster(cands, merge):
    """Merge candidates whose levels are within `merge` into zones."""
    zones = []
    for lvl, src, w in sorted(cands, key=lambda t: t[0]):
        if zones and lvl - zones[-1]["_max"] <= merge:
            z = zones[-1]
            z["members"].append((lvl, src, w))
            z["_min"] = min(z["_min"], lvl)
            z["_max"] = max(z["_max"], lvl)
        else:
            zones.append({"members": [(lvl, src, w)], "_min": lvl, "_max": lvl})
    for z in zones:
        wsum = sum(w for _, _, w in z["members"]) or 1e-9
        z["level"] = sum(lvl * w for lvl, _, w in z["members"]) / wsum
        z["families"] = {_FAMILY.get(src, src) for _, src, _ in z["members"]}
        z["sources"] = sorted({src for _, src, _ in z["members"]})
    return zones


def _touches(zone, highs, lows, closes, atr):
    """How many bars pierced the zone band, and how many of those reversed."""
    lo, hi = zone["_min"] - 0.15 * atr, zone["_max"] + 0.15 * atr
    n = len(closes)
    touches = rejections = 0
    last_touch_age = None
    for i in range(n):
        if lows[i] <= hi and highs[i] >= lo:
            touches += 1
            last_touch_age = (n - 1 - i)
            # reversal: within 2 bars, close moved >= 0.3*ATR away from the zone
            for j in (i + 1, i + 2):
                if j < n and abs(closes[j] - zone["level"]) >= 0.3 * atr:
                    rejections += 1
                    break
    return touches, rejections, last_touch_age


def _strength(zone, *, highs, lows, closes, atr, vwap, chain, mode, weights):
    price = closes[-1]
    lvl = zone["level"]
    touches, rejections, age = _touches(zone, highs, lows, closes, atr)
    n = len(closes)

    c = {
        "confluence": min(1.0, len(zone["families"]) / 4.0),
        "touch_quality": (rejections / touches) if touches else 0.0,
        "rejection_count": min(1.0, rejections / 3.0),
        "recency": (1.0 - age / n) if age is not None else 0.0,
        "htf_agree": 1.0 if ("htf" in zone["families"] or "prevday" in zone["families"]) else 0.0,
        "oi_backing": 0.0,
        "oi_change_backing": 0.0,
        "vwap_prox": (1.0 - min(1.0, abs(lvl - vwap) / (1.5 * atr))) if (vwap is not None and atr) else 0.0,
    }
    if mode == "index" and chain:
        near = min(chain, key=lambda r: abs((_num(r.get("strike")) or 1e18) - lvl))
        tot = sum((_num((r.get("ce") or {}).get("oi")) or 0) + (_num((r.get("pe") or {}).get("oi")) or 0)
                  for r in chain) or 1.0
        c["oi_backing"] = min(1.0, ((_num((near.get("ce") or {}).get("oi")) or 0)
                                    + (_num((near.get("pe") or {}).get("oi")) or 0)) / (tot / len(chain)) / 3.0)
        chg = abs(_num((near.get("ce") or {}).get("oi_chg")) or 0) + abs(_num((near.get("pe") or {}).get("oi_chg")) or 0)
        c["oi_change_backing"] = min(1.0, chg / (tot / len(chain)) / 2.0)

    w = dict(weights)
    if mode != "index":                       # redistribute OI weight to structure
        spill = w.pop("oi_backing", 0) + w.pop("oi_change_backing", 0)
        w["oi_backing"] = w["oi_change_backing"] = 0.0
        s = sum(v for k, v in w.items() if v > 0) or 1e-9
        w = {k: (v + spill * v / s if v > 0 else 0.0) for k, v in w.items()}

    score = 100.0 * sum(c[k] * w.get(k, 0.0) for k in c)
    return {
        "level": round(lvl, 2),
        "zone": [round(zone["_min"], 2), round(zone["_max"], 2)],
        "strength": round(max(0.0, min(100.0, score)), 1),
        "touches": touches, "rejections": rejections,
        "dist_atr": round((lvl - price) / atr, 3) if atr else None,
        "sources": zone["sources"], "families": sorted(zone["families"]),
        "components": {k: round(v, 3) for k, v in c.items()},
    }


def compute_sr(bars_by_tf: dict, *, chain: list | None = None,
               prev_day: dict | None = None, mode: str = "index",
               config: dict | None = None) -> dict:
    cfg = config or {}
    weights = {**_W, **(cfg.get("weights") or {})}
    round_step = cfg.get("round_step", 50.0 if mode == "index" else 5.0)

    tf, bars = _pick_tf(bars_by_tf)
    H, L, C, V, n = _bars(bars)
    if n < 12:
        return {"status": "DATA_UNAVAILABLE", "reason": f"only {n} bars on {tf}",
                "model_version": MODEL_VERSION, "mode": mode}

    price = C[-1]
    atr = _atr(H, L, C, n, min(14, n - 1)) or (mean(h - l for h, l in zip(H, L)) or price * 0.001)
    atr = max(atr, price * 1e-4)
    vwap = _vwap(H, L, C, V, n) if any(V) else None

    cands = _candidates(H, L, C, V, atr, vwap, mode=mode, chain=chain,
                        prev_day=prev_day, round_step=round_step)
    # add HTF swing levels
    for htf in ("15m", "30m"):
        hb = bars_by_tf.get(htf) or []
        if len(hb) >= 12:
            hH, hL, *_ = _bars(hb)
            for i, lvl, k in _swings(hH, hL):
                cands.append((lvl, "htf_swing_high" if k == "H" else "htf_swing_low", 1.1))

    zones = _cluster(cands, merge=max(0.25 * atr, price * 1e-4))
    scored = [_strength(z, highs=H, lows=L, closes=C, atr=atr, vwap=vwap,
                        chain=chain, mode=mode, weights=weights) for z in zones]

    below = [z for z in scored if z["level"] < price - 1e-6]
    above = [z for z in scored if z["level"] > price + 1e-6]

    def _best(cands_, near_first=True):
        if not cands_:
            return None
        window = [z for z in cands_ if abs(z["dist_atr"] or 99) <= 4.0] or cands_
        return sorted(window, key=lambda z: (-z["strength"], abs(z["dist_atr"] or 99)))[0]

    support = _best(below)
    resistance = _best(above)

    return {
        "status": "OK", "model_version": MODEL_VERSION, "mode": mode, "tf": tf,
        "price": round(price, 2), "atr": round(atr, 3),
        "vwap": round(vwap, 2) if vwap is not None else None,
        "support": support, "resistance": resistance,
        "support_strength": support["strength"] if support else 0.0,
        "resistance_strength": resistance["strength"] if resistance else 0.0,
        "levels": sorted(scored, key=lambda z: z["level"]),
        "n_zones": len(scored),
    }
