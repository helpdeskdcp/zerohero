"""
Reversal detector — resistance→support / support→resistance turns.

Looks for price tagging a recent swing level and getting rejected:
  * BEARISH reversal AT RESISTANCE — bar's high tags the swing high, then
    closes well back below it (rejection wick), momentum rolling over.
  * BULLISH reversal AT SUPPORT   — bar's low tags the swing low, then
    closes well back above it, momentum turning up.

Deterministic, same idiom as signal_engine. Returns a directional call plus
concrete entry / target / stop derived from ATR and the level itself, so it
can be acted on directly (as an options CE/PE pick).
"""
import math

from .engines.signal_engine import (
    _num, _round, _ema_series, _rsi, _atr,
)


def _swings(highs, lows, lookback, edge):
    """Swing high/low over `lookback` bars, excluding the last `edge` bars
    (those are the 'test'). Returns (resistance, support)."""
    a, b = max(0, len(highs) - lookback - edge), len(highs) - edge
    if b - a < 5:
        return None, None
    return max(highs[a:b]), min(lows[a:b])


def detect_reversal(candles: list, cfg: dict | None = None) -> dict:
    cfg = cfg or {}
    C = {
        "lookback": int(_num(cfg.get("lookback")) or 30),
        "edge": int(_num(cfg.get("edge")) or 3),
        "tag_atr": _num(cfg.get("tag_atr")) or 0.5,     # how close to the level counts as a "tag"
        "reject_frac": _num(cfg.get("reject_frac")) or 0.45,  # close must retrace this much of the bar range
        "rsi_hot": _num(cfg.get("rsi_hot")) or 62,
        "rsi_cold": _num(cfg.get("rsi_cold")) or 38,
        "atr_stop_mult": _num(cfg.get("atr_stop_mult")) or 0.8,
        "atr_t1_mult": _num(cfg.get("atr_t1_mult")) or 1.5,
        "atr_t2_mult": _num(cfg.get("atr_t2_mult")) or 2.8,
        "min_candles": int(_num(cfg.get("min_candles")) or 25),
    }
    norm = []
    for k in candles or []:
        if isinstance(k, list):
            if len(k) < 5:
                continue
            o, h, l, c = k[1], k[2], k[3], k[4]
        else:
            o, h, l, c = k.get("o"), k.get("h"), k.get("l"), k.get("c")
        try:
            o, h, l, c = float(o), float(h), float(l), float(c)
        except (TypeError, ValueError):
            continue
        if all(math.isfinite(x) for x in (o, h, l, c)):
            norm.append((o, h, l, c))

    if len(norm) < C["min_candles"]:
        return {"reversal": None, "reason": [f"insufficient candles ({len(norm)})"]}

    highs = [x[1] for x in norm]
    lows = [x[2] for x in norm]
    closes = [x[3] for x in norm]
    n = len(norm)
    price = closes[-1]

    atr = _atr(highs, lows, closes, n, 14) or 0
    rsi = _rsi(closes, 14)
    rsi_prev = _rsi(closes[:-1], 14) if n > 16 else rsi
    ema20 = _ema_series(closes, 20)
    resistance, support = _swings(highs, lows, C["lookback"], C["edge"])
    if not atr or resistance is None or support is None:
        return {"reversal": None, "reason": ["no ATR / swing levels"]}

    tag = C["tag_atr"] * atr
    # look at the last `edge` bars for the test-and-reject
    tail = norm[-C["edge"]:]
    bar_hi = max(x[1] for x in tail)
    bar_lo = min(x[2] for x in tail)
    last_o, last_h, last_l, last_c = norm[-1]
    rng = max(1e-9, last_h - last_l)

    out = {"reversal": None, "level": None, "kind": None, "price": _round(price),
           "atr": _round(atr), "resistance": _round(resistance), "support": _round(support),
           "rsi": _round(rsi, 1), "reason": []}

    # --- bearish reversal at resistance ---
    tagged_r = bar_hi >= resistance - tag
    reject_r = (last_h - last_c) / rng >= C["reject_frac"] and last_c < last_o
    mom_r = (rsi is not None and (rsi_prev or rsi) >= C["rsi_hot"] and rsi <= (rsi_prev or rsi))
    if tagged_r and reject_r and (mom_r or price < (ema20 or price)):
        entry = price
        sl = _round(max(bar_hi, resistance) + 0.2 * atr)
        t1 = _round(entry - C["atr_t1_mult"] * atr)
        t2 = _round(min(entry - C["atr_t2_mult"] * atr, support))
        rr = _round((entry - t1) / (sl - entry)) if sl and sl > entry else None
        out.update({
            "reversal": "BEARISH", "kind": "AT_RESISTANCE", "level": _round(resistance),
            "direction": "SELL", "option": "PE", "entry": _round(entry),
            "stop": sl, "target_1": t1, "target_2": t2, "risk_reward": rr,
            "confidence": _round(min(95, 45 + (25 if mom_r else 0) + (20 if reject_r else 0)), 0),
            "reason": [f"tagged resistance {_round(resistance)} (bar hi {_round(bar_hi)})",
                       f"rejection: close {_round(last_c)} back {_round(100*(last_h-last_c)/rng)}% of bar",
                       f"RSI {_round(rsi,1)} rolling from {_round(rsi_prev,1)}"],
        })
        return out

    # --- bullish reversal at support ---
    tagged_s = bar_lo <= support + tag
    reject_s = (last_c - last_l) / rng >= C["reject_frac"] and last_c > last_o
    mom_s = (rsi is not None and (rsi_prev or rsi) <= C["rsi_cold"] and rsi >= (rsi_prev or rsi))
    if tagged_s and reject_s and (mom_s or price > (ema20 or price)):
        entry = price
        sl = _round(min(bar_lo, support) - 0.2 * atr)
        t1 = _round(entry + C["atr_t1_mult"] * atr)
        t2 = _round(max(entry + C["atr_t2_mult"] * atr, resistance))
        rr = _round((t1 - entry) / (entry - sl)) if sl and sl < entry else None
        out.update({
            "reversal": "BULLISH", "kind": "AT_SUPPORT", "level": _round(support),
            "direction": "BUY", "option": "CE", "entry": _round(entry),
            "stop": sl, "target_1": t1, "target_2": t2, "risk_reward": rr,
            "confidence": _round(min(95, 45 + (25 if mom_s else 0) + (20 if reject_s else 0)), 0),
            "reason": [f"tagged support {_round(support)} (bar lo {_round(bar_lo)})",
                       f"rejection: close {_round(last_c)} up {_round(100*(last_c-last_l)/rng)}% of bar",
                       f"RSI {_round(rsi,1)} turning from {_round(rsi_prev,1)}"],
        })
        return out

    out["reason"] = [f"no reversal — price {_round(price)} between S {_round(support)} / R {_round(resistance)}"]
    return out
