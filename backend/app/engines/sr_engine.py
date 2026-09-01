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

import math
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
        _ce_oi = lambda r: _num((r.get("ce") or {}).get("oi")) or 0.0
        _pe_oi = lambda r: _num((r.get("pe") or {}).get("oi")) or 0.0
        tot_ce = sum(_ce_oi(r) for r in chain) or 1.0
        tot_pe = sum(_pe_oi(r) for r in chain) or 1.0
        ce_wall = max(chain, key=_ce_oi)
        pe_wall = max(chain, key=_pe_oi)
        # A strike with no OI is not a wall — never let missing/zero OI seed a
        # phantom candidate (would otherwise land on chain[0] with weight 0.5).
        if _ce_oi(ce_wall) > 0:
            cand.append((_num(ce_wall.get("strike")),
                         "oi_wall_ce", 0.5 + 0.8 * (_ce_oi(ce_wall) / tot_ce)))
        if _pe_oi(pe_wall) > 0:
            cand.append((_num(pe_wall.get("strike")),
                         "oi_wall_pe", 0.5 + 0.8 * (_pe_oi(pe_wall) / tot_pe)))
        _ce_chg = lambda r: _num((r.get("ce") or {}).get("oi_chg")) or 0.0
        _pe_chg = lambda r: _num((r.get("pe") or {}).get("oi_chg")) or 0.0
        ce_write, pe_write = max(chain, key=_ce_chg), max(chain, key=_pe_chg)
        if _ce_chg(ce_write) > 0:
            cand.append((_num(ce_write.get("strike")), "oi_write_ce", 0.6))
        if _pe_chg(pe_write) > 0:
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


def _oi_wall_diag(chain, price, symbol):
    """Per-side OI-wall trace: the raw wall, its distance from spot, the
    distance-weighted view, and why it does / does not dominate. Read-only —
    does not feed the score. Distances are % of spot (option-agnostic)."""
    if not chain:
        return {}
    exp = _chain_expiry(chain)
    tot_ce = sum(_num((r.get("ce") or {}).get("oi")) or 0 for r in chain) or 1.0
    tot_pe = sum(_num((r.get("pe") or {}).get("oi")) or 0 for r in chain) or 1.0

    def side(key, tot):
        ranked = sorted(
            ({"strike": _num(r.get("strike")),
              "oi": _num((r.get(key) or {}).get("oi")) or 0.0,
              "oi_chg": _num((r.get(key) or {}).get("oi_chg"))}
             for r in chain if _num(r.get("strike")) is not None),
            key=lambda x: -x["oi"])
        out = []
        for x in ranked[:3]:
            if x["oi"] <= 0:
                continue
            dist_pct = round(100.0 * (x["strike"] - price) / price, 3) if price else None
            raw = round(x["oi"] / tot, 4)                     # share of side OI
            # illustrative distance-weighted view (NOT used by the score):
            # linear decay to 0 by 5% of spot away.
            decay = max(0.0, 1.0 - abs(dist_pct or 0) / 5.0)
            out.append({
                "strike": x["strike"], "oi": x["oi"], "oi_chg": x["oi_chg"],
                "dist_pct": dist_pct, "raw_wall_score": raw,
                "dist_weighted_score": round(raw * decay, 4),
            })
        return out

    return {
        "symbol": symbol, "expiry": exp, "spot": round(price, 2) if price else None,
        "call_walls": side("ce", tot_ce), "put_walls": side("pe", tot_pe),
        "note": "OI is ~17% of the strength score; walls that do not coincide "
                "with a price-structure level score low and are not selected. "
                "dist_weighted_score is illustrative only, it does not feed the model.",
    }


def _chain_expiry(chain):
    for r in chain or []:
        e = (r.get("ce") or {}).get("expiry") or (r.get("pe") or {}).get("expiry")
        if e:
            return e
    return None


# --------------------------------------------------------------------------- GEX
# Gamma-Exposure profile (GEX_SR_SPEC.md, phase A / v1a). Read-only diagnostic:
# it is surfaced in `sr_diag.gex` + as four scalars on the return, and is NOT
# fed into `_candidates` / `_strength`, so no number the strategy uses changes.
# Ported formula from vibe/analysis/gex.py:
#     shape(K) = BS_gamma(K) * (call_OI(K) - put_OI(K))
# `spot * lot_size * 100` is constant across strikes -> dropped (it scales the
# magnitude only, never the flip / pin / sign).
_SQRT2 = math.sqrt(2.0)
_SQRT2PI = math.sqrt(2.0 * math.pi)
_DEFAULT_T_YEARS = 4.0 / 365.25          # weekly-ish; v1b passes the real T in
# picked-tf -> ~bars per NSE trading year (390-min day * 252), realized-vol proxy
_TF_BPY = {"1m": 98280, "3m": 32760, "5m": 19656, "15m": 6552, "30m": 3276, "1h": 1638}


def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / _SQRT2PI


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _bs_d1(S, K, T, sigma):
    return (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))


def _bs_price(S, K, T, sigma, is_call):
    """European price, r = 0 (intraday). None on a degenerate input."""
    if not (S > 0 and K > 0 and T > 0 and sigma > 0):
        return None
    d1 = _bs_d1(S, K, T, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    if is_call:
        return S * _norm_cdf(d1) - K * _norm_cdf(d2)
    return K * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def _bs_gamma(S, K, T, sigma):
    """d(delta)/dS -- identical for the call and the put at a strike. r = 0."""
    if not (S > 0 and K > 0 and T > 0 and sigma > 0):
        return 0.0
    g = _norm_pdf(_bs_d1(S, K, T, sigma)) / (S * sigma * math.sqrt(T))
    return g if (g == g and g not in (float("inf"), float("-inf"))) else 0.0


def _solve_iv(S, K, T, price, is_call, *, lo=0.03, hi=3.0, tol=1e-4, iters=64):
    """Bounded bisection: sigma s.t. BS(sigma) ~= price. None if not bracketed."""
    if not (price and price > 0 and S > 0 and K > 0 and T > 0):
        return None
    plo, phi = _bs_price(S, K, T, lo, is_call), _bs_price(S, K, T, hi, is_call)
    if plo is None or phi is None or not (plo < price < phi):
        return None
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        pm = _bs_price(S, K, T, mid, is_call)
        if pm is None:
            return None
        if abs(pm - price) < tol * max(1.0, price):
            return mid
        lo, hi = (mid, hi) if pm < price else (lo, mid)
    return 0.5 * (lo + hi)


def _gex_profile(chain, price, atr, *, t_years=None, bars_per_year=19656, cfg=None):
    """Read-only gamma-exposure profile. NEVER raises. See GEX_SR_SPEC.md.

    Returns {status, flip, pin, total_shape, regime_sign(-1|0|1), regime,
             sigma, sigma_src, t_years, flip_in_range, pin_in_range,
             per_strike:[{strike, ce_oi, pe_oi, gamma, shape}]}.
    `flip` = first zero-crossing of shape(K) scanning low->high (either
    direction -- vibe's one-way rule misses the common pe-heavy-below chain),
    interpolated. `pin` = strike of max |shape| (gamma-weighted OI magnet).
    """
    cfg = cfg or {}
    out = {"status": "n/a", "flip": None, "pin": None, "total_shape": None,
           "regime_sign": 0, "regime": "NEUTRAL", "sigma": None, "sigma_src": None,
           "t_years": None, "flip_in_range": False, "pin_in_range": False,
           "per_strike": []}
    try:
        if not price or price <= 0:
            out["status"] = "no_spot"
            return out
        rows = []
        for r in chain or []:
            k = _num(r.get("strike"))
            if k is None or k <= 0:
                continue
            ce, pe = r.get("ce") or {}, r.get("pe") or {}
            ce_oi = _num(ce.get("oi")) or 0.0
            pe_oi = _num(pe.get("oi")) or 0.0
            if ce_oi <= 0 and pe_oi <= 0:
                continue
            rows.append((k, ce_oi, pe_oi, _num(ce.get("ltp")), _num(pe.get("ltp"))))
        rows.sort(key=lambda x: x[0])
        if len(rows) < 4:
            out["status"] = "thin_chain"
            return out

        T = t_years if (t_years and t_years > 0) else _DEFAULT_T_YEARS
        out["t_years"] = round(T, 6)
        lo = float(cfg.get("iv_floor", 0.03))
        hi = float(cfg.get("iv_cap", 3.0))

        atm = min(rows, key=lambda x: abs(x[0] - price))
        sigma = None
        for is_call, px in ((True, atm[3]), (False, atm[4])):
            sigma = _solve_iv(price, atm[0], T, px, is_call, lo=lo, hi=hi)
            if sigma:
                out["sigma_src"] = "atm_solve"
                break
        if not sigma and atr and atr > 0 and bars_per_year > 0:
            sigma = max(lo, min(hi, (atr / price) * math.sqrt(bars_per_year)))
            out["sigma_src"] = "realized"
        if not sigma:
            out["status"] = "no_vol"
            return out
        out["sigma"] = round(sigma, 4)

        per = []
        for k, ce_oi, pe_oi, _cl, _pl in rows:
            g = _bs_gamma(price, k, T, sigma)
            per.append({"strike": k, "ce_oi": ce_oi, "pe_oi": pe_oi,
                        "gamma": g, "shape": g * (ce_oi - pe_oi)})

        total = sum(p["shape"] for p in per)
        absmass = sum(abs(p["shape"]) for p in per) or 1.0
        out["total_shape"] = round(total, 2)
        sign = (1 if total > 0 else -1) if abs(total) > 0.10 * absmass else 0
        out["regime_sign"] = sign
        # vibe's hypothesis (to be tested in A2): + = dealer long gamma / pinning,
        # - = short gamma / breakout. Neutral labels until the evidence is in.
        out["regime"] = {1: "CALL_SKEW", -1: "PUT_SKEW", 0: "NEUTRAL"}[sign]

        pin_row = max(per, key=lambda p: abs(p["shape"]))
        out["pin"] = round(pin_row["strike"], 2)

        flip = None
        for i in range(1, len(per)):
            a, b = per[i - 1]["shape"], per[i]["shape"]
            if (a > 0 >= b) or (a < 0 <= b):
                ka, kb = per[i - 1]["strike"], per[i]["strike"]
                flip = ka + (a / (a - b)) * (kb - ka) if a != b else kb
                break
        out["flip"] = round(flip, 2) if flip is not None else None

        md = float(cfg.get("max_dist_atr", 4.0))
        out["flip_in_range"] = bool(flip is not None and atr and abs(flip - price) <= md * atr)
        out["pin_in_range"] = bool(atr and abs(pin_row["strike"] - price) <= md * atr)
        out["per_strike"] = [{"strike": p["strike"], "ce_oi": p["ce_oi"], "pe_oi": p["pe_oi"],
                              "gamma": round(p["gamma"], 9), "shape": round(p["shape"], 2)}
                             for p in per]
        out["status"] = "ok"
        return out
    except Exception as e:                       # never let a diagnostic break S/R
        out["status"] = f"error: {type(e).__name__}"
        return out


def _level_diag(z, chain, price, symbol, kind):
    if not z:
        return None
    d = {"kind": kind, "symbol": symbol, "spot": round(price, 2) if price else None,
         "expiry": _chain_expiry(chain),
         "level": z["level"], "zone": z["zone"], "strength": z["strength"],
         "dist_pct": round(100.0 * (z["level"] - price) / price, 3) if price else None,
         "dist_atr": z["dist_atr"], "touches": z["touches"], "rejections": z["rejections"],
         "families": z["families"], "sources": z["sources"],
         "components": z["components"]}
    if chain:
        near = min(chain, key=lambda r: abs((_num(r.get("strike")) or 1e18) - z["level"]))
        ce, pe = near.get("ce") or {}, near.get("pe") or {}
        d["nearest_strike"] = _num(near.get("strike"))
        d["call_oi"] = _num(ce.get("oi"))
        d["put_oi"] = _num(pe.get("oi"))
        d["oi_change"] = (_num(ce.get("oi_chg")), _num(pe.get("oi_chg")))
    d["reason"] = (
        f"selected {kind}: {'|'.join(z['families'])} confluence, "
        f"{z['touches']} touches / {z['rejections']} rejections, "
        f"oi_backing={z['components'].get('oi_backing')}")
    return d


def compute_sr(bars_by_tf: dict, *, chain: list | None = None,
               prev_day: dict | None = None, mode: str = "index",
               symbol: str | None = None, config: dict | None = None) -> dict:
    cfg = config or {}
    weights = {**_W, **(cfg.get("weights") or {})}
    round_step = cfg.get("round_step", 50.0 if mode == "index" else 5.0)

    tf, bars = _pick_tf(bars_by_tf)
    H, L, C, V, n = _bars(bars)
    if n < 12:
        return {"status": "DATA_UNAVAILABLE", "reason": f"only {n} bars on {tf}",
                "model_version": MODEL_VERSION, "mode": mode,
                "vwap": None, "vwap_status": "insufficient_data",
                "vwap_reason": f"only {n} bars on {tf} (need >= 12)"}

    price = C[-1]
    atr = _atr(H, L, C, n, min(14, n - 1)) or (mean(h - l for h, l in zip(H, L)) or price * 0.001)
    atr = max(atr, price * 1e-4)
    # VWAP is volume-weighted: it is only real when the bar series carries
    # traded volume. An NSE cash index (NIFTY, BANKNIFTY, ...) has none, so
    # VWAP is legitimately unavailable there — never fabricate one.
    vsum = sum(v for v in V if v)
    if not any(V):
        vwap, vwap_status = None, "invalid_volume"
        vwap_reason = ("bar series carries no traded volume — an NSE cash index "
                       "has no volume, so a volume-weighted price cannot be computed")
    elif vsum <= 0:
        vwap, vwap_status, vwap_reason = None, "invalid_volume", "total bar volume <= 0"
    else:
        vwap = _vwap(H, L, C, V, n)
        if vwap is None:
            vwap_status, vwap_reason = "invalid_volume", "volume sum <= 0 in _vwap"
        else:
            vwap_status, vwap_reason = "available", ""

    # Gamma-exposure profile -- read-only diagnostic, NOT fed to _candidates /
    # _strength (v1a). Always computed in index mode; no-op elsewhere.
    gcfg = cfg.get("gex") or {}
    gex = (_gex_profile(chain, price, atr, t_years=gcfg.get("t_years"),
                        bars_per_year=_TF_BPY.get(tf, 19656), cfg=gcfg)
           if mode == "index" else
           {"status": "n/a", "flip": None, "pin": None, "regime_sign": 0,
            "regime": "NEUTRAL", "sigma": None})

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
        "vwap_status": vwap_status, "vwap_reason": vwap_reason,
        "support": support, "resistance": resistance,
        "support_strength": support["strength"] if support else 0.0,
        "resistance_strength": resistance["strength"] if resistance else 0.0,
        "levels": sorted(scored, key=lambda z: z["level"]),
        "n_zones": len(scored),
        # GEX scalars for the snapshot -- read-only, do NOT gate anything (v1a)
        "gex_flip": gex.get("flip"), "gex_pin": gex.get("pin"),
        "gex_regime_sign": gex.get("regime_sign"), "gex_sigma": gex.get("sigma"),
        # read-only diagnostics (do not feed the model / strategy)
        "sr_diag": {
            "vwap": round(vwap, 2) if vwap is not None else None,
            "vwap_status": vwap_status, "vwap_reason": vwap_reason,
            "oi_walls": _oi_wall_diag(chain, price, symbol) if mode == "index" else {},
            "gex": gex,
            "support": _level_diag(support, chain, price, symbol, "support"),
            "resistance": _level_diag(resistance, chain, price, symbol, "resistance"),
        },
    }
