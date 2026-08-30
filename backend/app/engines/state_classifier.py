"""
Four-state market classifier + false-breakout / sweep detector (spec-2, spec-6).

Consumes closed candles (multi-tf) + the P1 S/R result and classifies the tape
into exactly one of:

  SUPPORT_REVERSAL     U-turn up    at a support zone   -> BULLISH (BUY CE cand.)
  SUPPORT_BREAKDOWN    continuation down through support -> BEARISH (BUY PE cand.)
  RESISTANCE_REVERSAL  U-turn down  at a resistance zone -> BEARISH (BUY PE cand.)
  RESISTANCE_BREAKOUT  continuation up through resistance -> BULLISH (BUY CE cand.)
  NONE                 nothing clean

Every classification carries a 0-100 `state_score` (weighted component sum,
spec-3) and a `false_risk` block (spec-6: wick-only, low-volume, immediate
reclaim, failed retest, liquidity sweep, momentum divergence). A LIKELY_FALSE
verdict forces NONE unless a confirmed retest-and-hold overrides.

Pure & deterministic. Weights are seed values exposed for P5 calibration.
"""
from __future__ import annotations

from typing import Optional

from .signal_engine import _atr, _ema_series, _rsi, _sma

MODEL_VERSION = "state-classifier-v1"

_W = {
    "price_action": 0.20, "level_strength": 0.16, "volume": 0.12, "oi": 0.12,
    "momentum": 0.14, "vwap": 0.08, "atr": 0.08, "htf": 0.06, "retest": 0.04,
}
BULLISH = {"SUPPORT_REVERSAL", "RESISTANCE_BREAKOUT"}
BEARISH = {"SUPPORT_BREAKDOWN", "RESISTANCE_REVERSAL"}


def _num(x):
    try:
        f = float(x)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _ohlc(bars):
    O, H, L, C, V = [], [], [], [], []
    for k in bars or []:
        if isinstance(k, (list, tuple)):
            o, h, l, c = (_num(k[i]) for i in (1, 2, 3, 4))
            v = _num(k[5]) if len(k) > 5 else 0.0
        else:
            o, h, l, c = (_num(k.get(x)) for x in ("o", "h", "l", "c"))
            v = _num(k.get("v")) or 0.0
        if None in (o, h, l, c):
            continue
        O.append(o); H.append(h); L.append(l); C.append(c); V.append(v or 0.0)
    return O, H, L, C, V


def _roc(closes, n=3):
    if len(closes) < n + 1 or closes[-(n + 1)] == 0:
        return 0.0
    return (closes[-1] - closes[-(n + 1)]) / abs(closes[-(n + 1)]) * 100.0


def _htf_trend(bars_by_tf):
    for tf in ("30m", "15m"):
        b = bars_by_tf.get(tf) or []
        _, _, _, C, _ = _ohlc(b)
        if len(C) < 12:
            continue
        e20, e50 = _ema_series(C, min(20, len(C) - 1)), _ema_series(C, min(50, len(C) - 1))
        if e20 is None or e50 is None:
            continue
        slope = C[-1] - C[max(0, len(C) - 6)]
        if C[-1] > e20 > e50 and slope > 0:
            return "UP", tf
        if C[-1] < e20 < e50 and slope < 0:
            return "DOWN", tf
        return "FLAT", tf
    return "UNKNOWN", None


def _oi_at(chain, level, want):
    """want='ce_write' | 'pe_write' | 'ce_oi' | 'pe_oi' -> 0..1 near `level`."""
    if not chain or level is None:
        return 0.0
    near = min(chain, key=lambda r: abs((_num(r.get("strike")) or 1e18) - level))
    ce, pe = near.get("ce") or {}, near.get("pe") or {}
    tot = sum((_num((r.get("ce") or {}).get("oi")) or 0) + (_num((r.get("pe") or {}).get("oi")) or 0)
              for r in chain) or 1.0
    per = tot / max(1, len(chain))
    if want == "ce_write":
        return max(0.0, min(1.0, (_num(ce.get("oi_chg")) or 0) / per / 1.5))
    if want == "pe_write":
        return max(0.0, min(1.0, (_num(pe.get("oi_chg")) or 0) / per / 1.5))
    if want == "ce_oi":
        return max(0.0, min(1.0, (_num(ce.get("oi")) or 0) / per / 3.0))
    if want == "pe_oi":
        return max(0.0, min(1.0, (_num(pe.get("oi")) or 0) / per / 3.0))
    return 0.0


def _vol_ratio(V, idx, look=10):
    if idx <= 0:
        return 1.0
    base = _sma(V[max(0, idx - look):idx], min(look, idx))
    if not base or base <= 0:
        return 1.0 if (V[idx] or 0) == 0 else 2.0
    return (V[idx] or 0) / base


def _score(comp, weights):
    return round(100.0 * sum(comp.get(k, 0.0) * weights.get(k, 0.0) for k in weights), 1)


def _false_risk(flags: list) -> dict:
    fset = set(flags)
    if {"wick_only", "reclaimed"} & fset or len(fset) >= 3:
        verdict, pen = "LIKELY_FALSE", 0.8
    elif len(fset) == 2:
        verdict, pen = "SUSPECT", 0.5
    elif len(fset) == 1:
        verdict, pen = "SUSPECT", 0.25
    else:
        verdict, pen = "CLEAN", 0.0
    return {"flags": sorted(fset), "verdict": verdict, "penalty": pen,
            "score": round(100 * (1 - pen), 1)}


# --------------------------------------------------------------------------- #
def _eval_break(side, zone, O, H, L, C, V, atr, vwap, rsi, roc, htf, chain, cfg):
    """side='RESISTANCE' -> breakout up ; side='SUPPORT' -> breakdown down."""
    up = side == "RESISTANCE"
    lvl = zone["level"]
    n = len(C)
    brk_min = cfg.get("brk_atr_min", 0.30)
    tol = cfg.get("test_tol_atr", 0.35) * atr

    close_beyond = (C[-1] > zone["zone"][1] + brk_min * atr) if up else (C[-1] < zone["zone"][0] - brk_min * atr)
    atr_dist = (C[-1] - lvl) / atr if up else (lvl - C[-1]) / atr

    # locate the breakout bar within the last ~4 closed bars (a fresh break)
    bi = None
    for i in range(max(1, n - 4), n):
        crossed = (C[i - 1] <= lvl < C[i]) if up else (C[i - 1] >= lvl > C[i])
        if crossed:
            bi = i
    fresh = bi is not None
    if not fresh and close_beyond and abs(atr_dist) <= 1.5:
        bi = n - 1                                   # close beyond, very recently
        fresh = True

    flags, follow, retest_held, vr = [], False, None, 1.0
    if bi is not None:
        vr = _vol_ratio(V, bi)
        body_ok = (C[bi] > O[bi]) if up else (C[bi] < O[bi])
        pierced = (H[bi] > lvl) if up else (L[bi] < lvl)
        if pierced and not (close_beyond and body_ok):
            flags.append("wick_only")
        if vr < cfg.get("vol_expansion_min", 1.0):
            flags.append("low_volume")
        for j in range(bi + 1, n):
            if (C[j] > C[bi]) if up else (C[j] < C[bi]):
                follow = True
            back_inside = (C[j] < zone["zone"][1]) if up else (C[j] > zone["zone"][0])
            if back_inside:
                flags.append("reclaimed")
                break
        # retest: a later bar dipped back to the level, then held / failed
        for j in range(bi + 1, n):
            touched = (L[j] <= lvl + tol) if up else (H[j] >= lvl - tol)
            if touched:
                retest_held = (C[j] > lvl) if up else (C[j] < lvl)
                if not retest_held:
                    flags.append("failed_retest")
                break
    # momentum divergence: new price extreme without RSI confirmation
    if rsi is not None:
        if up and C[-1] >= max(C) and rsi < 55:
            flags.append("momentum_divergence")
        if (not up) and C[-1] <= min(C) and rsi > 45:
            flags.append("momentum_divergence")

    fr = _false_risk(flags)
    comp = {
        "price_action": 1.0 if close_beyond else max(0.0, min(1.0, atr_dist / brk_min)),
        "level_strength": zone["strength"] / 100.0,
        "volume": max(0.0, min(1.0, vr / 1.5)),
        "oi": _oi_at(chain, lvl, "ce_write" if up else "pe_write"),
        "momentum": max(0.0, min(1.0, (roc / 0.15) if up else (-roc / 0.15))),
        "vwap": 1.0 if (vwap is None) else (1.0 if ((C[-1] > vwap) == up) else 0.2),
        "atr": max(0.0, min(1.0, atr_dist / 1.0)),
        "htf": 1.0 if ((htf == "UP") == up and htf in ("UP", "DOWN")) else (0.5 if htf in ("FLAT", "UNKNOWN") else 0.0),
        "retest": 1.0 if retest_held else (0.0 if retest_held is False else 0.4),
    }
    raw = _score(comp, _W)
    if not fresh:                       # not a live break -> heavily discounted
        raw *= 0.25
        comp = {k: v * 0.25 for k, v in comp.items()}
    state = "RESISTANCE_BREAKOUT" if up else "SUPPORT_BREAKDOWN"
    return {"state": state, "raw_score": raw, "components": comp, "false_risk": fr,
            "confirmation": {"close_beyond": bool(close_beyond),
                             "atr_distance": round(atr_dist, 3),
                             "follow_through": follow, "retest_held": retest_held},
            "anchor": {"side": side, **zone}}


def _eval_reversal(side, zone, O, H, L, C, V, atr, vwap, rsi, roc, htf, chain, cfg):
    """side='SUPPORT' -> U-turn up ; side='RESISTANCE' -> U-turn down."""
    up = side == "SUPPORT"
    lvl = zone["level"]
    n = len(C)
    tol = cfg.get("test_tol_atr", 0.35) * atr

    # find the test bar: recent bar that reached into the zone
    ti = None
    for i in range(max(0, n - 6), n):
        reached = (L[i] <= zone["zone"][1] + tol) if up else (H[i] >= zone["zone"][0] - tol)
        if reached:
            ti = i
    tested = ti is not None
    flags = []
    wick = rej_depth = 0.0
    rev_candle = subsequent = False
    if tested:
        b = ti
        if up:
            wick = max(0.0, (min(O[b], C[b]) - L[b]) / atr)      # lower wick
            rej_depth = max(0.0, (min(O[b], C[b]) - min(zone["zone"][0], L[b])) / atr)
            rev_candle = C[b] > O[b] and C[b] > zone["zone"][0]
        else:
            wick = max(0.0, (H[b] - max(O[b], C[b])) / atr)       # upper wick
            rej_depth = max(0.0, (max(zone["zone"][1], H[b]) - max(O[b], C[b])) / atr)
            rev_candle = C[b] < O[b] and C[b] < zone["zone"][1]
        for j in range(b + 1, n):
            subsequent = (C[j] > C[b]) if up else (C[j] < C[b])
            break
        # liquidity sweep: sharp pierce then full reversal, wide range
        rng = (H[b] - L[b]) / atr
        pierced = (L[b] < zone["zone"][0]) if up else (H[b] > zone["zone"][1])
        if pierced and rev_candle and rng > 1.2:
            flags.append("liquidity_sweep")
        vr = _vol_ratio(V, b)
        if vr < cfg.get("vol_expansion_min", 1.0):
            flags.append("low_volume")
    if not tested or not rev_candle:
        flags.append("no_rejection")

    # momentum turn
    mom = 0.0
    if rsi is not None:
        mom = (max(0.0, (45 - rsi) / 25.0) + max(0.0, roc / 0.15)) / 2 if up \
            else (max(0.0, (rsi - 55) / 25.0) + max(0.0, -roc / 0.15)) / 2
        mom = min(1.0, mom)
        if up and rsi < 20 and roc < -0.1:
            pass
    fr = _false_risk(flags)
    comp = {
        "price_action": min(1.0, 0.5 * wick + 0.5 * (1.0 if rev_candle else 0.0)),
        "level_strength": zone["strength"] / 100.0,
        "volume": max(0.0, min(1.0, _vol_ratio(V, ti) / 1.5)) if tested else 0.0,
        "oi": _oi_at(chain, lvl, "pe_write" if up else "ce_write"),
        "momentum": mom,
        "vwap": 1.0 if (vwap is None) else (1.0 if ((C[-1] > vwap) == up) else 0.3),
        "atr": max(0.0, min(1.0, rej_depth / 0.8)),
        "htf": 0.0 if ((htf == "UP" and not up) or (htf == "DOWN" and up)) else (1.0 if htf in ("FLAT", "UNKNOWN") else 0.7),
        "retest": 1.0 if subsequent else 0.2,
    }
    raw = _score(comp, _W)
    state = "SUPPORT_REVERSAL" if up else "RESISTANCE_REVERSAL"
    return {"state": state, "raw_score": raw, "components": comp, "false_risk": fr,
            "confirmation": {"tested": tested, "rejection_wick_atr": round(wick, 3),
                             "reversal_candle": rev_candle, "subsequent_confirm": subsequent},
            "anchor": {"side": side, **zone}}


def classify(bars_by_tf: dict, sr: dict, *, chain: list | None = None,
             config: dict | None = None) -> dict:
    cfg = config or {}
    weights = {**_W, **(cfg.get("weights") or {})}
    if not sr or sr.get("status") != "OK":
        return {"state": "NONE", "direction": "NONE", "state_score": 0.0,
                "reason": ["S/R unavailable"], "model_version": MODEL_VERSION}

    tf = sr.get("tf", "5m")
    bars = bars_by_tf.get(tf) or bars_by_tf.get("5m") or bars_by_tf.get("3m") or []
    O, H, L, C, V = _ohlc(bars)
    if len(C) < 15:
        return {"state": "NONE", "direction": "NONE", "state_score": 0.0,
                "reason": [f"only {len(C)} bars"], "model_version": MODEL_VERSION}

    atr = _num(sr.get("atr")) or _atr(H, L, C, len(C), min(14, len(C) - 1)) or (C[-1] * 1e-3)
    atr = max(atr, C[-1] * 1e-4)
    vwap = _num(sr.get("vwap"))
    rsi = _rsi(C, 14)
    roc = _roc(C, 3)
    htf, htf_tf = _htf_trend(bars_by_tf)

    price = C[-1]
    zones, seen = [], set()
    for z in list(sr.get("levels") or []) + [sr.get("support"), sr.get("resistance")]:
        if not z or z.get("dist_atr") is None:
            continue
        key = round(z["level"], 2)
        if key in seen or abs(z["dist_atr"]) > cfg.get("zone_window_atr", 6.0):
            continue
        seen.add(key)
        zones.append(z)

    brk_win = cfg.get("breakout_window_atr", 2.5)

    def crossed_up(lvl):
        return any(C[i - 1] <= lvl < C[i] for i in range(max(1, len(C) - 4), len(C)))

    def crossed_dn(lvl):
        return any(C[i - 1] >= lvl > C[i] for i in range(max(1, len(C) - 4), len(C)))

    raw_cands = []
    for z in zones:
        d = z["dist_atr"]                       # (level - price)/atr ; <0 => below price
        # breakout: only for a level still nearby that was crossed recently
        if abs(d) <= brk_win and (d <= 0.25 or crossed_up(z["level"])):
            raw_cands.append(_eval_break("RESISTANCE", z, O, H, L, C, V, atr, vwap, rsi, roc, htf, chain, cfg))
        if abs(d) <= brk_win and (d >= -0.25 or crossed_dn(z["level"])):
            raw_cands.append(_eval_break("SUPPORT", z, O, H, L, C, V, atr, vwap, rsi, roc, htf, chain, cfg))
        # reversals: no hard distance gate -> the internal "recently tested +
        # rejection candle" check gates it (a non-fitting zone scores ~0 with a
        # no_rejection flag).
        raw_cands.append(_eval_reversal("SUPPORT", z, O, H, L, C, V, atr, vwap, rsi, roc, htf, chain, cfg))
        raw_cands.append(_eval_reversal("RESISTANCE", z, O, H, L, C, V, atr, vwap, rsi, roc, htf, chain, cfg))

    # one candidate per state — keep the best-anchored instance
    for c in raw_cands:
        c["state_score"] = round(c["raw_score"] * (1.0 - c["false_risk"]["penalty"]), 1)
        if c["false_risk"]["verdict"] == "LIKELY_FALSE" and not c["confirmation"].get("retest_held"):
            c["state_score"] = min(c["state_score"], 20.0)
    best: dict[str, dict] = {}
    for c in raw_cands:
        if c["state"] not in best or c["state_score"] > best[c["state"]]["state_score"]:
            best[c["state"]] = c
    cands = list(best.values())

    min_score = cfg.get("min_state_score", 45.0)
    ok = [c for c in cands if c["state_score"] >= min_score]
    if not ok:
        best = max(cands, key=lambda c: c["state_score"]) if cands else None
        return {"state": "NONE", "direction": "NONE",
                "state_score": best["state_score"] if best else 0.0,
                "htf_trend": htf, "candidates": cands,
                "reason": ["no state above threshold"], "model_version": MODEL_VERSION}

    # prefer higher score; tie-break toward HTF alignment
    def keyf(c):
        aligned = (c["state"] in BULLISH and htf == "UP") or (c["state"] in BEARISH and htf == "DOWN")
        return (c["state_score"], 1 if aligned else 0)

    win = max(ok, key=keyf)
    direction = "BULLISH" if win["state"] in BULLISH else "BEARISH"
    reason = [
        f"{win['state']} @ {win['anchor']['side']} {win['anchor']['level']} "
        f"(zone strength {win['anchor']['strength']})",
        f"score {win['state_score']} raw {win['raw_score']} "
        f"false_risk {win['false_risk']['verdict']} {win['false_risk']['flags']}",
        f"htf {htf}",
    ]
    return {
        "state": win["state"], "direction": direction,
        "state_score": win["state_score"], "raw_score": win["raw_score"],
        "components": win["components"], "false_risk": win["false_risk"],
        "confirmation": win["confirmation"], "anchor": win["anchor"],
        "htf_trend": htf, "htf_tf": htf_tf, "rsi": round(rsi, 1) if rsi is not None else None,
        "roc_pct": round(roc, 3), "candidates": cands,
        "reason": reason, "model_version": MODEL_VERSION,
    }
