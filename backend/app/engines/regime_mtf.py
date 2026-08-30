"""
Market-regime detector (spec-12) + multi-timeframe alignment score (spec-7).

Both pure & deterministic; consume the same {tf: closed-bars} dict the S/R and
state engines use.

detect_regime()  -> one of TRENDING_UP / TRENDING_DOWN / RANGE / HIGH_VOLATILITY
                    / LOW_VOLATILITY / BREAKOUT_REGIME / REVERSAL_REGIME /
                    UNSTABLE  (+ adx, atr_pct, trend, vol_state, confidence)

mtf_alignment()  -> signed alignment in [-100, +100] across 1m/3m/5m/15m/30m
                    with higher timeframes weighted more, a `conflict` flag when
                    the fast TFs oppose a strong HTF read, and per-TF detail.
"""
from __future__ import annotations

from statistics import mean, pstdev

from .signal_engine import _adx, _atr, _ema_series

MODEL_VERSION = "regime-mtf-v1"

_TF_ORDER = ("1m", "3m", "5m", "15m", "30m")
_TF_WEIGHT = {"1m": 0.10, "3m": 0.15, "5m": 0.22, "15m": 0.26, "30m": 0.27}


def _num(x):
    try:
        f = float(x)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _cl(bars):
    O, H, L, C = [], [], [], []
    for k in bars or []:
        if isinstance(k, (list, tuple)):
            o, h, l, c = (_num(k[i]) for i in (1, 2, 3, 4))
        else:
            o, h, l, c = (_num(k.get(x)) for x in ("o", "h", "l", "c"))
        if None in (o, h, l, c):
            continue
        O.append(o); H.append(h); L.append(l); C.append(c)
    return O, H, L, C


def _tf_dir(bars):
    """Per-TF directional read -> (-1|0|+1, strength 0..1)."""
    O, H, L, C = _cl(bars)
    n = len(C)
    if n < 12:
        return 0, 0.0
    e_f = _ema_series(C, min(9, n - 1))
    e_s = _ema_series(C, min(21, n - 1))
    if e_f is None or e_s is None:
        return 0, 0.0
    slope = C[-1] - C[max(0, n - 6)]
    sep = abs(e_f - e_s) / (C[-1] or 1) * 100.0        # EMA separation, %
    up = C[-1] > e_f > e_s and slope > 0
    dn = C[-1] < e_f < e_s and slope < 0
    strength = min(1.0, sep / 0.15 + abs(slope) / (C[-1] * 0.003 or 1) * 0.3)
    if up:
        return 1, strength
    if dn:
        return -1, strength
    return 0, min(1.0, strength * 0.4)


def mtf_alignment(bars_by_tf: dict, config: dict | None = None) -> dict:
    cfg = config or {}
    weights = {**_TF_WEIGHT, **(cfg.get("tf_weight") or {})}
    per = {}
    signed = 0.0
    wsum = 0.0
    for tf in _TF_ORDER:
        d, s = _tf_dir(bars_by_tf.get(tf) or [])
        per[tf] = {"dir": d, "strength": round(s, 3)}
        w = weights.get(tf, 0.0)
        signed += d * s * w
        wsum += w
    align = round(100.0 * signed / (wsum or 1e-9), 1)

    htf = mean([per[tf]["dir"] * per[tf]["strength"] for tf in ("15m", "30m")]) \
        if all(bars_by_tf.get(tf) for tf in ("15m", "30m")) else 0.0
    ltf = mean([per[tf]["dir"] * per[tf]["strength"] for tf in ("1m", "3m")])
    conflict = bool(htf and ltf and (htf > 0.25) != (ltf > 0.25) and abs(htf) > 0.25 and abs(ltf) > 0.25 and (htf * ltf < 0))
    htf_dominant = bool(abs(htf) >= 0.35)
    if conflict:
        align = round(align * 0.4, 1)          # fast TF must not override strong HTF

    direction = "BULLISH" if align > 12 else ("BEARISH" if align < -12 else "NONE")
    return {
        "alignment": align, "magnitude": abs(align), "direction": direction,
        "per_tf": per, "conflict": conflict, "htf_dominant": htf_dominant,
        "htf_bias": round(htf, 3), "ltf_bias": round(ltf, 3),
        "model_version": MODEL_VERSION,
    }


def detect_regime(bars_by_tf: dict, config: dict | None = None) -> dict:
    cfg = config or {}
    tf = cfg.get("regime_tf", "5m")
    bars = bars_by_tf.get(tf) or bars_by_tf.get("3m") or bars_by_tf.get("1m") or []
    O, H, L, C = _cl(bars)
    n = len(C)
    if n < 20:
        return {"regime": "UNSTABLE", "confidence": 0.0, "reason": [f"only {n} bars on {tf}"],
                "model_version": MODEL_VERSION}

    price = C[-1]
    atr = _atr(H, L, C, n, min(14, n - 1)) or (mean(h - l for h, l in zip(H, L)) or price * 1e-3)
    atr_pct = atr / price * 100.0 if price else 0.0
    adx_d = _adx(H, L, C, n, min(14, n - 1)) or {}
    adx = adx_d.get("adx")
    e20, e50 = _ema_series(C, min(20, n - 1)), _ema_series(C, min(50, n - 1))

    # recent vs prior range (compression / expansion)
    look = min(10, n // 3)
    recent_rng = max(H[-look:]) - min(L[-look:])
    prior_rng = max(H[-2 * look:-look]) - min(L[-2 * look:-look]) if n >= 2 * look else recent_rng
    compression = recent_rng / prior_rng if prior_rng else 1.0
    last_bar_rng = (H[-1] - L[-1]) / atr if atr else 0.0

    # volatility state, relative to its own recent distribution
    tr_series = [abs(H[i] - L[i]) for i in range(max(0, n - 40), n)]
    v_mean = mean(tr_series) if tr_series else atr
    v_sd = pstdev(tr_series) if len(tr_series) > 2 else 0.0
    z_vol = (atr - v_mean) / v_sd if v_sd else 0.0
    hi_atr_pct = cfg.get("high_atr_pct", 0.45)      # absolute ceiling (index %)
    lo_atr_pct = cfg.get("low_atr_pct", 0.035)
    if z_vol > 1.0 or atr_pct > hi_atr_pct:
        vol_state = "HIGH"
    elif z_vol < -0.8 or atr_pct < lo_atr_pct:
        vol_state = "LOW"
    else:
        vol_state = "NORMAL"

    # choppiness: EMA(9/21) sign flips in the last ~20 bars
    flips = 0
    ef = es = None
    prev = None
    for i in range(max(9, n - 20), n):
        sub = C[:i + 1]
        a, b = _ema_series(sub, 9), _ema_series(sub, 21)
        if a is None or b is None:
            continue
        cur = 1 if a > b else -1
        if prev is not None and cur != prev:
            flips += 1
        prev = cur

    trend = "UP" if (e20 and e50 and price > e20 > e50) else ("DOWN" if (e20 and e50 and price < e20 < e50) else "FLAT")

    reason = [f"adx={round(adx, 1) if adx is not None else None} atr%={round(atr_pct, 3)} "
              f"compression={round(compression, 2)} lastbar_atr={round(last_bar_rng, 2)} "
              f"vol_z={round(z_vol, 2)} ema_flips={flips} trend={trend}"]

    # --- classify (priority order) ---
    choppy = flips >= 4
    trending = adx is not None and adx >= 25 and trend in ("UP", "DOWN")
    if choppy and vol_state == "HIGH" and not trending:
        regime, conf = "UNSTABLE", 0.5
    elif compression < 0.55 and last_bar_rng >= 1.4 and adx is not None and adx >= 18 and not choppy:
        regime, conf = "BREAKOUT_REGIME", 0.65
    elif trending and trend == "UP":
        regime, conf = "TRENDING_UP", min(1.0, 0.5 + (adx - 25) / 40)
    elif trending and trend == "DOWN":
        regime, conf = "TRENDING_DOWN", min(1.0, 0.5 + (adx - 25) / 40)
    elif adx is not None and adx < 20 and vol_state != "HIGH":
        # low directional strength + not violent -> a range (choppy EMAs are
        # normal range behaviour, not instability)
        regime, conf = ("LOW_VOLATILITY", 0.6) if vol_state == "LOW" else ("RANGE", 0.62)
    elif 2 <= flips <= 3 and vol_state != "LOW" and (adx is None or adx < 24):
        regime, conf = "REVERSAL_REGIME", 0.5
    elif vol_state == "HIGH":
        regime, conf = "HIGH_VOLATILITY", 0.6
    elif vol_state == "LOW":
        regime, conf = "LOW_VOLATILITY", 0.55
    elif choppy:
        regime, conf = "RANGE", 0.5
    else:
        regime, conf = "UNSTABLE", 0.4

    return {
        "regime": regime, "confidence": round(conf, 2),
        "adx": round(adx, 1) if adx is not None else None,
        "atr": round(atr, 3), "atr_pct": round(atr_pct, 3),
        "trend": trend, "vol_state": vol_state, "vol_z": round(z_vol, 2),
        "compression": round(compression, 2), "ema_flips_20": flips,
        "tf": tf, "reason": reason, "model_version": MODEL_VERSION,
    }
