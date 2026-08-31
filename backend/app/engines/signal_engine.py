"""
AI-SIGNAL-ENGINE (deterministic core)
Ported 1:1 from the n8n Code node logic. No network calls, no fabricated
data. All math derived from input candles. NOT ML — RULE_BASED_PROBABILITY.
"""
import math
import time
from datetime import datetime, timezone

MODEL_VERSION = "signal-engine-rule-based-v1"


def _num(x):
    try:
        if x is None:
            return None
        f = float(x)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _round(x, d=2):
    if x is None or not math.isfinite(x):
        return None
    p = 10 ** d
    return round(x * p) / p


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _sma(arr, p):
    if len(arr) < p:
        return None
    return sum(arr[-p:]) / p


def _ema_series(arr, p):
    if len(arr) < p:
        return None
    k = 2 / (p + 1)
    e = sum(arr[:p]) / p
    for i in range(p, len(arr)):
        e = arr[i] * k + e * (1 - k)
    return e


def _rsi(arr, p=14):
    if len(arr) < p + 1:
        return None
    gain = 0.0
    loss = 0.0
    for i in range(len(arr) - p, len(arr)):
        d = arr[i] - arr[i - 1]
        if d >= 0:
            gain += d
        else:
            loss -= d
    ag = gain / p
    al = loss / p
    if ag == 0 and al == 0:
        return 50.0  # flat series -> neutral, not 100
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)


def _atr(highs, lows, closes, n, p=14):
    if n < p + 1:
        return None
    s = 0.0
    for i in range(n - p, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        s += tr
    return s / p


def _vwap(highs, lows, closes, vols, n):
    pv = 0.0
    vv = 0.0
    for i in range(n):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        pv += tp * vols[i]
        vv += vols[i]
    return pv / vv if vv > 0 else None


def _macd(closes):
    e12 = _ema_series(closes, 12)
    e26 = _ema_series(closes, 26)
    if e12 is None or e26 is None:
        return None
    return e12 - e26


def _adx(highs, lows, closes, n, p=14):
    if n < p + 2:
        return None
    plus_dm = 0.0
    minus_dm = 0.0
    tr_sum = 0.0
    for i in range(n - p, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        plus_dm += up if (up > dn and up > 0) else 0
        minus_dm += dn if (dn > up and dn > 0) else 0
        tr_sum += max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    if tr_sum == 0:
        return None
    p_di = 100 * plus_dm / tr_sum
    m_di = 100 * minus_dm / tr_sum
    dx = 0 if (p_di + m_di == 0) else 100 * abs(p_di - m_di) / (p_di + m_di)
    return {"adx": dx, "pDI": p_di, "mDI": m_di}


def _structure(norm, n):
    if n < 10:
        return "UNKNOWN"
    a = norm[n - 10:n - 5]
    b = norm[n - 5:]
    aH = max(k["h"] for k in a)
    aL = min(k["l"] for k in a)
    bH = max(k["h"] for k in b)
    bL = min(k["l"] for k in b)
    if bH > aH and bL > aL:
        return "HIGHER_HIGH_HIGHER_LOW"
    if bH < aH and bL < aL:
        return "LOWER_HIGH_LOWER_LOW"
    if bH > aH and bL < aL:
        return "EXPANSION"
    return "RANGE"


def run_signal_engine(inp: dict) -> dict:
    inp = inp or {}
    cfg = inp.get("config") or {}
    C = {
        "prob_min": _num(cfg.get("prob_min")) or 55,
        "rr_min": _num(cfg.get("rr_min")) or 1.5,
        "atr_target1_mult": _num(cfg.get("atr_target1_mult")) or 1.0,
        "atr_target2_mult": _num(cfg.get("atr_target2_mult")) or 2.0,
        "atr_stop_mult": _num(cfg.get("atr_stop_mult")) or 1.2,
        "atr_trail_mult": _num(cfg.get("atr_trail_mult")) or 1.0,
        "min_candles": _num(cfg.get("min_candles")) or 30,
        "max_stale_sec": _num(cfg.get("max_stale_sec")) or 900,
        "min_move_pct": _num(cfg.get("min_move_pct")) or 0.05,
    }

    def base_out(status, extra=None):
        out = {
            "timestamp": _now_iso(),
            "market": inp.get("market"),
            "symbol": inp.get("symbol"),
            "instrument": inp.get("instrument"),
            "timeframe": inp.get("timeframe"),
            "expiry": inp.get("expiry"),
            "strike": _num(inp.get("strike")),
            "source": inp.get("source"),
            "direction": "NONE",
            "entry_zone": {},
            "target_1": None, "target_2": None, "final_target": None,
            "stop_loss": None, "break_even": None, "trailing_stop": None,
            "probability": None, "confidence": None, "risk_reward": None,
            # `direction_lean` / `lean_score` are populated on NO_TRADE only: the
            # directional read that was computed but then discarded, and its raw
            # uncalibrated sigmoid-of-evidence score (0-100). They are NOT a
            # probability of trade success — `probability` stays None when there
            # is no actionable trade.
            "direction_lean": None, "lean_score": None,
            "market_regime": None,
            "decision": status,
            "reason": [], "invalidation": [],
            "facts": {}, "calculations": {},
            "model_version": MODEL_VERSION,
        }
        if extra:
            out.update(extra)
        return out

    candles = inp.get("candles") or []
    norm = []
    for k in candles:
        if isinstance(k, list):
            t, o, h, l, c = k[0], k[1], k[2], k[3], k[4]
            v = k[5] if len(k) > 5 else 0
        else:
            t = k.get("t", k.get("time", k.get("timestamp")))
            o, h, l, c = k.get("o"), k.get("h"), k.get("l"), k.get("c")
            v = k.get("v", k.get("volume", 0))
        try:
            o, h, l, c = float(o), float(h), float(l), float(c)
            v = float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            continue
        if all(math.isfinite(x) for x in (o, h, l, c)):
            norm.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v})

    if len(norm) < C["min_candles"]:
        return base_out("DATA_UNAVAILABLE", {
            "reason": [f"FACT: insufficient candles ({len(norm)} < {int(C['min_candles'])})"],
        })

    last_t = norm[-1]["t"]
    stale_sec = None
    if last_t:
        try:
            if isinstance(last_t, (int, float)):
                tms = last_t if last_t > 1e12 else last_t * 1000
            else:
                tms = datetime.fromisoformat(str(last_t).replace("Z", "+00:00")).timestamp() * 1000
            if math.isfinite(tms):
                stale_sec = round((time.time() * 1000 - tms) / 1000)
        except Exception:
            stale_sec = None

    closes = [k["c"] for k in norm]
    highs = [k["h"] for k in norm]
    lows = [k["l"] for k in norm]
    vols = [k["v"] for k in norm]
    n = len(norm)

    price = closes[-1]
    ema20 = _ema_series(closes, 20)
    ema50 = _ema_series(closes, min(50, n - 1))
    sma20 = _sma(closes, 20)
    rsi14 = _rsi(closes, 14)
    atr14 = _atr(highs, lows, closes, n, 14)
    vw = _vwap(highs, lows, closes, vols, n)
    macdv = _macd(closes)
    adxv = _adx(highs, lows, closes, n, 14)

    sw_win = min(20, n)
    recent_highs = highs[n - sw_win:]
    recent_lows = lows[n - sw_win:]
    resistance = max(recent_highs)
    support = min(recent_lows)

    close_max = max(closes)
    close_min = min(closes)
    close_range = close_max - close_min
    close_range_pct = (close_range / price) * 100 if price else 0
    is_flat_market = close_range == 0 or close_range_pct < C["min_move_pct"]

    struct = _structure(norm, n)

    adx_val = adxv["adx"] if adxv else None
    vol_pct = (atr14 / price) * 100 if (atr14 is not None and price) else None
    if is_flat_market:
        regime = "FLAT"
    elif adx_val is not None:
        if adx_val >= 25 and ema20 is not None and ema50 is not None and ema20 > ema50 and price > ema20:
            regime = "TRENDING_UP"
        elif adx_val >= 25 and ema20 is not None and ema50 is not None and ema20 < ema50 and price < ema20:
            regime = "TRENDING_DOWN"
        elif adx_val < 18:
            regime = "RANGE"
        else:
            regime = "TRANSITION"
    else:
        regime = "UNSTABLE"
    if not is_flat_market and vol_pct is not None and vol_pct > 3:
        regime = "HIGH_VOLATILITY" if regime == "RANGE" else regime + "_HIGH_VOL"

    ev = 0.0
    reasons = []

    def add(cond, w, label):
        nonlocal ev
        if cond is None:
            return
        if cond:
            ev += w
            reasons.append(f"CALC(+{w}): {label}")
        else:
            ev -= w
            reasons.append(f"CALC(-{w}): NOT {label}")

    add(ema20 > ema50 if (ema20 is not None and ema50 is not None) else None, 1.0, "EMA20>EMA50")
    add(price > ema20 if (price is not None and ema20 is not None) else None, 0.8, "price>EMA20")
    add(price > vw if vw is not None else None, 0.6, "price>VWAP")
    add(rsi14 > 50 if rsi14 is not None else None, 0.6, "RSI>50")
    add(macdv > 0 if macdv is not None else None, 0.7, "MACD>0")
    add(True if struct == "HIGHER_HIGH_HIGHER_LOW" else (False if struct == "LOWER_HIGH_LOWER_LOW" else None),
        1.0, "bullish market structure")
    if adxv:
        add(adxv["pDI"] > adxv["mDI"], 0.7, "+DI>-DI")

    p_up = 1 / (1 + math.exp(-ev))
    prob_up_pct = _round(p_up * 100, 1)
    prob_down_pct = _round((1 - p_up) * 100, 1)

    direction_raw = "BUY" if p_up >= 0.5 else "SELL"
    prob_dir = prob_up_pct if direction_raw == "BUY" else prob_down_pct

    confidence = _round(min(100, abs(ev) / 4.4 * 100), 1)

    entry = price
    t1 = t2 = final_t = sl = be = trail = rr = None
    if atr14 is not None and atr14 > 0:
        if direction_raw == "BUY":
            sl = _round(price - C["atr_stop_mult"] * atr14)
            swing_sl = _round(support)
            if swing_sl is not None and swing_sl > sl and swing_sl < price:
                sl = swing_sl
            t1 = _round(price + C["atr_target1_mult"] * atr14)
            t2 = _round(price + C["atr_target2_mult"] * atr14)
            final_t = _round(max(t2, resistance))
            be = _round(price)
            trail = _round(C["atr_trail_mult"] * atr14)
            rr = _round((t1 - price) / (price - sl)) if (price - sl) > 0 else None
        else:
            sl = _round(price + C["atr_stop_mult"] * atr14)
            swing_sl = _round(resistance)
            if swing_sl is not None and swing_sl < sl and swing_sl > price:
                sl = swing_sl
            t1 = _round(price - C["atr_target1_mult"] * atr14)
            t2 = _round(price - C["atr_target2_mult"] * atr14)
            final_t = _round(min(t2, support))
            be = _round(price)
            trail = _round(C["atr_trail_mult"] * atr14)
            rr = _round((price - t1) / (sl - price)) if (sl - price) > 0 else None

    no_trade_reasons = []
    if is_flat_market:
        no_trade_reasons.append(
            f"FACT: flat market — close range {_round(close_range_pct, 3)}% < {C['min_move_pct']}% (no directional movement)")
    if stale_sec is not None and stale_sec > C["max_stale_sec"]:
        no_trade_reasons.append(f"FACT: data stale ({stale_sec}s > {int(C['max_stale_sec'])}s)")
    if prob_dir is None or prob_dir < C["prob_min"]:
        no_trade_reasons.append(f"CALC: probability {prob_dir}% < threshold {C['prob_min']}%")
    if rr is None or rr < C["rr_min"]:
        no_trade_reasons.append(f"CALC: risk/reward {rr} < minimum {C['rr_min']}")
    if regime in ("UNSTABLE", "TRANSITION"):
        no_trade_reasons.append(f"CALC: market regime unsuitable ({regime})")
    if atr14 is None:
        no_trade_reasons.append("FACT: ATR unavailable")

    facts = {
        "price": price, "staleSec": stale_sec, "candleCount": n,
        "support": _round(support), "resistance": _round(resistance),
        "close_range_pct": _round(close_range_pct, 3),
    }
    calculations = {
        "ema20": _round(ema20), "ema50": _round(ema50), "sma20": _round(sma20),
        "rsi14": _round(rsi14, 1), "atr14": _round(atr14), "vwap": _round(vw),
        "macd": _round(macdv), "adx": _round(adx_val, 1) if adx_val is not None else None,
        "volatility_pct": _round(vol_pct, 2) if vol_pct is not None else None,
        "structure": struct, "evidence": _round(ev, 2), "flat_market": is_flat_market,
    }

    if no_trade_reasons:
        return base_out("NO_TRADE", {
            "direction": "NONE",
            # Preserve the discarded directional read explicitly instead of
            # letting it leak out through `probability`. `lean_score` is the raw
            # sigmoid-of-evidence in that lean's direction (uncalibrated,
            # rule-based, NOT P(trade wins)); `confidence` is the evidence
            # magnitude meter (|ev| rescaled to 0-100). `probability` is None:
            # a rejected signal has no trade to assign a probability to.
            "direction_lean": direction_raw,
            "lean_score": prob_dir,
            "market_regime": regime,
            "probability": None,
            "confidence": confidence,
            "risk_reward": rr,
            "reason": no_trade_reasons + reasons,
            "facts": facts, "calculations": calculations,
        })

    return base_out("TRADE", {
        "direction": direction_raw,
        "entry_zone": {
            "low": _round(min(entry, entry - (atr14 * 0.1))),
            "high": _round(max(entry, entry + (atr14 * 0.1))),
            "ref": _round(entry),
        },
        "target_1": t1, "target_2": t2, "final_target": final_t,
        "stop_loss": sl, "break_even": be, "trailing_stop": trail,
        "probability": prob_dir, "confidence": confidence, "risk_reward": rr,
        "market_regime": regime,
        "reason": [f"INTERPRETATION: {direction_raw} favored under {regime}"] + reasons,
        "invalidation": [
            f"Close below stop_loss {sl} invalidates the setup" if direction_raw == "BUY"
            else f"Close above stop_loss {sl} invalidates the setup",
            f"Regime change away from {regime} invalidates the setup",
        ],
        "facts": facts, "calculations": calculations,
    })
