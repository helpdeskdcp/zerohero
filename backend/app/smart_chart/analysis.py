"""Smart Chart analysis: deterministic, structured, honest about what the data source does NOT provide.

The deterministic block (trend / structure / levels / ATR / reference levels) is computed from closed candles only and
always works. The optional AI narrative receives only this compact JSON summary (never raw history or screenshots) and may
not add facts: its output is shown separately, with `confidence` labelled as a model estimate - NOT a probability of profit.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time

PIVOT_K = 3
ATR_N = 14
# Facts the configured data source (Upstox historical/intraday candles) does not supply. Never fabricated.
UNAVAILABLE = [
    {"item": "order book / market depth", "reason": "Upstox candles carry no depth"},
    {"item": "bid / ask / spread", "reason": "candles only; no quote stream is consumed here"},
    {"item": "open interest", "reason": "not part of index/equity candles"},
    {"item": "implied volatility / greeks / option chain", "reason": "not requested; use the Option Chain view"},
    {"item": "aggressor / order-flow delta", "reason": "no tick or aggressor data exists for this source"},
]


def ema(vals: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(vals)
    if len(vals) < n:
        return out
    k = 2 / (n + 1)
    e = sum(vals[:n]) / n
    out[n - 1] = e
    for i in range(n, len(vals)):
        e = vals[i] * k + e * (1 - k)
        out[i] = e
    return out


def atr(candles: list[dict], n: int = ATR_N) -> float | None:
    if len(candles) < n + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]["close"]
        trs.append(max(c["high"] - c["low"], abs(c["high"] - p), abs(c["low"] - p)))
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def pivots(candles: list[dict], k: int = PIVOT_K) -> list[dict]:
    """Confirmed swing points only (k bars on each side) - a pivot is known k bars late, never earlier."""
    out = []
    for i in range(k, len(candles) - k):
        hi, lo = candles[i]["high"], candles[i]["low"]
        if all(hi > candles[j]["high"] for j in range(i - k, i)) and all(hi >= candles[j]["high"] for j in range(i + 1, i + k + 1)):
            out.append({"i": i, "time": candles[i]["time"], "type": "H", "price": hi})
        if all(lo < candles[j]["low"] for j in range(i - k, i)) and all(lo <= candles[j]["low"] for j in range(i + 1, i + k + 1)):
            out.append({"i": i, "time": candles[i]["time"], "type": "L", "price": lo})
    return out


def _cluster(levels: list[float], tol: float) -> list[float]:
    levels = sorted(levels)
    groups: list[list[float]] = []
    for p in levels:
        if groups and abs(p - groups[-1][-1]) <= tol:
            groups[-1].append(p)
        else:
            groups.append([p])
    return [round(sum(g) / len(g), 2) for g in sorted(groups, key=len, reverse=True)]


def deterministic(candles: list[dict], meta: dict, signals: list[dict] | None = None, validation: dict | None = None) -> dict:
    closed = candles[:-1] if meta.get("last_bar_forming") and len(candles) > 1 else candles
    warnings = []
    if len(closed) < 60:
        warnings.append(f"only {len(closed)} closed bars; trend/structure estimates are weak")
    if meta.get("stale"):
        warnings.append(f"market data is stale: {meta.get('error')}")
    if not meta.get("volume_available"):
        warnings.append("volume is not published for indices; volume-based conclusions are not possible")
    out: dict = {"as_of_ms": closed[-1]["time"] if closed else None, "bars_used": len(closed), "warnings": warnings,
                 "unavailable": UNAVAILABLE, "data": {k: meta.get(k) for k in ("source", "transport", "native_interval", "aggregated", "market_open", "last_bar_forming", "stale", "volume_available")}}
    if len(closed) < 25:
        out["error"] = "not enough bars for analysis"
        return out
    closes = [c["close"] for c in closed]
    fast, slow = ema(closes, 20), ema(closes, 50)
    last, f, s = closes[-1], fast[-1], slow[-1]
    a = atr(closed)
    if f is not None and s is not None and a:
        slope = (fast[-1] - fast[-6]) / a if len(fast) > 6 and fast[-6] is not None else 0.0
        if last > f > s and slope > 0:
            direction = "UP"
        elif last < f < s and slope < 0:
            direction = "DOWN"
        else:
            direction = "SIDEWAYS"
        out["trend"] = {"direction": direction, "ema20": round(f, 2), "ema50": round(s, 2), "ema20_slope_atr": round(slope, 2),
                        "basis": "close vs EMA20 vs EMA50 and EMA20 slope over 5 bars (in ATR units)"}
    else:
        out["trend"] = {"direction": "UNKNOWN", "basis": "not enough history for EMA50"}
    pv = pivots(closed)
    highs, lows = [p for p in pv if p["type"] == "H"][-3:], [p for p in pv if p["type"] == "L"][-3:]
    if len(highs) >= 2 and len(lows) >= 2:
        hh, hl = highs[-1]["price"] > highs[-2]["price"], lows[-1]["price"] > lows[-2]["price"]
        lh, ll = highs[-1]["price"] < highs[-2]["price"], lows[-1]["price"] < lows[-2]["price"]
        state = "HH_HL (uptrend structure)" if hh and hl else "LH_LL (downtrend structure)" if lh and ll else "MIXED (range / transition)"
    else:
        state = "UNCLEAR"
    out["structure"] = {"state": state, "last_pivots": pv[-6:], "pivot_rule": f"{PIVOT_K} bars each side (confirmed {PIVOT_K} bars late)"}
    tol = (a or last * 0.002) * 0.5
    res = _cluster([p["price"] for p in pv[-24:] if p["type"] == "H" and p["price"] > last], tol)
    sup = _cluster([p["price"] for p in pv[-24:] if p["type"] == "L" and p["price"] < last], tol)
    out["levels"] = {"support": sorted([x for x in sup], reverse=True)[:3], "resistance": sorted(res)[:3]}
    out["volatility"] = {"atr14": round(a, 2) if a else None, "atr_pct": round(100 * a / last, 3) if a else None}
    win = closed[-21:-1]
    hi20, lo20 = max(c["high"] for c in win), min(c["low"] for c in win)
    cond = "BREAKOUT_UP" if closed[-1]["close"] > hi20 else "BREAKDOWN" if closed[-1]["close"] < lo20 else "INSIDE_20_BAR_RANGE"
    out["breakout"] = {"state": cond, "range_high_20": round(hi20, 2), "range_low_20": round(lo20, 2)}
    # Reference levels: arithmetic from the above, not a trade instruction.
    if a and out["trend"]["direction"] in ("UP", "DOWN"):
        long_ = out["trend"]["direction"] == "UP"
        swing = [p["price"] for p in lows[-2:]] if long_ else [p["price"] for p in highs[-2:]]
        stop = (min(swing) - 0.25 * a) if long_ and swing else (max(swing) + 0.25 * a) if swing else (last - 1.5 * a if long_ else last + 1.5 * a)
        risk = abs(last - stop)
        if risk < 0.5 * a:                      # too tight to be meaningful
            stop = last - 0.5 * a if long_ else last + 0.5 * a
            risk = 0.5 * a
        mult = 1 if long_ else -1
        out["reference"] = {"bias": "LONG" if long_ else "SHORT", "entry_ref": round(last, 2), "stop_ref": round(stop, 2),
                            "t1": round(last + mult * risk, 2), "t2": round(last + mult * 2 * risk, 2), "t3": round(last + mult * 3 * risk, 2),
                            "risk_per_unit": round(risk, 2), "rr": {"t1": 1.0, "t2": 2.0, "t3": 3.0},
                            "basis": "entry = last closed price; stop = last two swing points +/- 0.25 ATR (min 0.5 ATR); T1/T2/T3 = 1R/2R/3R. Reference arithmetic only - not a recommendation, not an order."}
    else:
        out["reference"] = None
    out["signals"] = signals or []
    out["validation"] = validation
    return out


def signals_from_plots(plots: list[dict], times: list[int], last_n: int = 5) -> list[dict]:
    """Most recent markers from boolean shape/char plots (plotshape / plotchar). A marker is a *condition*, not an order."""
    sigs = []
    for p in plots:
        if p["style"] not in ("shape", "char"):
            continue
        for i in range(len(p["values"]) - 1, -1, -1):
            if p["values"][i] is True:
                sigs.append({"plot": p["title"], "time": times[i], "bar": i, "text": p.get("text") or p.get("char") or p.get("shape"),
                             "location": p.get("location"), "bars_ago": len(times) - 1 - i})
                if sum(1 for s in sigs if s["plot"] == p["title"]) >= last_n:
                    break
    sigs.sort(key=lambda s: s["time"], reverse=True)
    return sigs[: last_n * 3]


# ------------------------------------------------------------------------------------ AI narrative (optional)
_ai_cache: dict[str, tuple[float, dict]] = {}
_ai_lock = threading.Lock()
AI_TTL_SEC = 120.0


def narrative(summary: dict, *, force: bool = False) -> dict:
    """Optional AI commentary over the deterministic summary. Never raises; the chart does not depend on it."""
    key = hashlib.sha256(json.dumps({k: summary.get(k) for k in ("as_of_ms", "trend", "structure", "levels", "breakout", "reference", "signals")}, sort_keys=True, default=str).encode()).hexdigest()
    now = time.time()
    with _ai_lock:
        hit = _ai_cache.get(key)
        if hit and not force and now - hit[0] < AI_TTL_SEC:
            return {**hit[1], "cached": True}
        for k in [k for k, v in _ai_cache.items() if now - v[0] > 600]:
            _ai_cache.pop(k, None)
    try:
        from ..ai import ai_client
        if not ai_client.is_available():
            return {"status": "UNAVAILABLE", "reason": "no AI provider configured", "narrative": None}
        compact = {k: summary.get(k) for k in ("trend", "structure", "levels", "volatility", "breakout", "reference", "signals", "validation", "warnings")}
        msgs = [
            {"role": "system", "content": "You are a cautious market analyst. Explain ONLY what the supplied JSON shows. Do not invent order-book, open-interest, "
             "IV, bid/ask or option-chain facts - they are not provided. Never promise profit. Reply as JSON: {\"summary\": str (<=60 words), "
             "\"signal_explanation\": str (<=60 words, or \"no active signal\"), \"risks\": [str, <=3 items], \"model_confidence\": integer 0-100}."},
            {"role": "user", "content": json.dumps(compact, default=str)},
        ]
        r = ai_client.chat_completion_json(messages=msgs, max_tokens=400, timeout=20)
        if r.status != "OK" or not isinstance(r.data, dict):
            return {"status": "ERROR", "reason": (r.error or r.status)[:200], "narrative": None}
        d = r.data
        out = {"status": "OK", "narrative": {"summary": str(d.get("summary", ""))[:600], "signal_explanation": str(d.get("signal_explanation", ""))[:600],
                                             "risks": [str(x)[:200] for x in (d.get("risks") or [])][:3]},
               "model_confidence": max(0, min(100, int(d.get("model_confidence", 0) or 0))),
               "confidence_kind": "model estimate - NOT a probability of profit", "model": r.model_used}
    except Exception as e:  # noqa: BLE001 - AI must never break the page
        return {"status": "ERROR", "reason": type(e).__name__, "narrative": None}
    with _ai_lock:
        _ai_cache[key] = (now, out)
    return out
