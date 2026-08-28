"""
AI-SCALP-ENGINE — deterministic fast-timeframe scalping signal engine.

Purpose-built for 1m / 3m candles. Same fail-closed, no-fabricated-data
philosophy as AI-SIGNAL-ENGINE, but tuned for scalping:

  * tight ATR-based ticks (target < 1x ATR, stop ~0.5x ATR)
  * hard time-in-trade cap (max_hold_sec) so a scalp never becomes a "hope trade"
  * micro-structure setups: VWAP reclaim, EMA pullback-continuation, momentum break
  * session-time filter (skip the open auction chop and the close)
  * volatility band — refuses to scalp a dead tape AND a knife

Emits the SAME contract shape as run_signal_engine so it flows unchanged
through the risk engine and the NO-TRADE gate, plus a few scalp-only keys:
  setup, strategy, max_hold_sec, tick_target, tick_stop, atr_pct
"""
import math
import time
from datetime import datetime, timezone, timedelta

from .signal_engine import (
    _num, _round, _now_iso, _sma, _ema_series, _rsi, _atr, _vwap, _macd,
)

MODEL_VERSION = "scalp-engine-rule-based-v1"

_SETUP_PRIORITY = ("VWAP_RECLAIM", "EMA_PULLBACK", "MOMENTUM_BREAK")


def _roc_pct(closes, n):
    """Rate of change over the last n bars, in percent of the older close."""
    if len(closes) < n + 1:
        return None
    old = closes[-(n + 1)]
    if old == 0:
        return None
    return (closes[-1] - old) / abs(old) * 100.0


def _parse_hhmm(s, default_min):
    try:
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return default_min


def _candle_minute_of_day(last_t, tz_offset_min):
    """Best-effort local minute-of-day for the last candle timestamp."""
    try:
        if isinstance(last_t, (int, float)):
            tms = last_t if last_t > 1e12 else last_t * 1000
            dt = datetime.fromtimestamp(tms / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(last_t).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
    except Exception:
        return None
    local = dt + timedelta(minutes=tz_offset_min)
    return local.hour * 60 + local.minute


def run_scalp_engine(inp: dict) -> dict:
    inp = inp or {}
    cfg = inp.get("config") or {}
    C = {
        "min_candles": _num(cfg.get("min_candles")) or 25,
        "max_stale_sec": _num(cfg.get("max_stale_sec")) or 150,
        "ema_fast": int(_num(cfg.get("ema_fast")) or 9),
        "ema_slow": int(_num(cfg.get("ema_slow")) or 21),
        "rsi_len": int(_num(cfg.get("rsi_len")) or 2),
        "rsi_buy_pullback": _num(cfg.get("rsi_buy_pullback")) or 15.0,
        "rsi_sell_pullback": _num(cfg.get("rsi_sell_pullback")) or 85.0,
        "roc_len": int(_num(cfg.get("roc_len")) or 3),
        "roc_min_pct": _num(cfg.get("roc_min_pct")) if _num(cfg.get("roc_min_pct")) is not None else 0.02,
        "breakout_lookback": int(_num(cfg.get("breakout_lookback")) or 5),
        "vol_expansion_mult": _num(cfg.get("vol_expansion_mult")) or 1.2,
        "atr_len": int(_num(cfg.get("atr_len")) or 14),
        "atr_target_mult": _num(cfg.get("atr_target_mult")) or 0.6,
        "atr_target2_mult": _num(cfg.get("atr_target2_mult")) or 1.1,
        "atr_stop_mult": _num(cfg.get("atr_stop_mult")) or 0.5,
        "atr_trail_mult": _num(cfg.get("atr_trail_mult")) or 0.4,
        "rr_min": _num(cfg.get("rr_min")) if _num(cfg.get("rr_min")) is not None else 1.0,
        "prob_min": _num(cfg.get("prob_min")) or 55.0,
        "min_atr_pct": _num(cfg.get("min_atr_pct")) if _num(cfg.get("min_atr_pct")) is not None else 0.03,
        "max_atr_pct": _num(cfg.get("max_atr_pct")) or 1.5,
        "max_spread_pct": _num(cfg.get("max_spread_pct")) or 0.35,
        "max_hold_sec": _num(cfg.get("max_hold_sec")) or 300,
        "ignore_session": bool(cfg.get("ignore_session")),
        "session_tz_offset_min": _num(cfg.get("session_tz_offset_min")) if _num(cfg.get("session_tz_offset_min")) is not None else 330,
        "session_start": cfg.get("session_start") or "09:20",
        "session_end": cfg.get("session_end") or "15:05",
        "skip_open_min": _num(cfg.get("skip_open_min")) if _num(cfg.get("skip_open_min")) is not None else 5,
        "skip_close_min": _num(cfg.get("skip_close_min")) if _num(cfg.get("skip_close_min")) is not None else 20,
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
            "strategy": "SCALP",
            "setup": None,
            "direction": "NONE",
            "entry_zone": {},
            "target_1": None, "target_2": None, "final_target": None,
            "stop_loss": None, "break_even": None, "trailing_stop": None,
            "tick_target": None, "tick_stop": None,
            "max_hold_sec": C["max_hold_sec"],
            "probability": None, "confidence": None, "risk_reward": None,
            "market_regime": None, "atr_pct": None,
            "decision": status,
            "reason": [], "invalidation": [],
            "facts": {}, "calculations": {},
            "model_version": MODEL_VERSION,
        }
        if extra:
            out.update(extra)
        return out

    # ---- normalise candles (accepts [t,o,h,l,c,v] or {t,o,h,l,c,v}) --------
    candles = inp.get("candles") or []
    norm = []
    for k in candles:
        if isinstance(k, list):
            if len(k) < 5:
                continue
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

    closes = [k["c"] for k in norm]
    opens = [k["o"] for k in norm]
    highs = [k["h"] for k in norm]
    lows = [k["l"] for k in norm]
    vols = [k["v"] for k in norm]
    n = len(norm)
    price = closes[-1]

    # ---- staleness -------------------------------------------------------
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

    # ---- indicators ----------------------------------------------------
    ema_f = _ema_series(closes, C["ema_fast"])
    ema_s = _ema_series(closes, C["ema_slow"])
    rsi_v = _rsi(closes, C["rsi_len"])
    rsi_prev = _rsi(closes[:-1], C["rsi_len"]) if n > C["rsi_len"] + 2 else None
    atr_v = _atr(highs, lows, closes, n, C["atr_len"])
    vwap_v = _vwap(highs, lows, closes, vols, n)
    vwap_prev = _vwap(highs[:-1], lows[:-1], closes[:-1], vols[:-1], n - 1) if n > 2 else None
    macd_v = _macd(closes)
    roc = _roc_pct(closes, C["roc_len"])

    vol_avg = _sma(vols[:-1], min(20, n - 1)) if n > 2 else None
    vol_now = vols[-1]
    vol_expansion = (vol_avg is not None and vol_avg > 0 and vol_now >= vol_avg * C["vol_expansion_mult"])

    atr_pct = (atr_v / price * 100.0) if (atr_v is not None and price) else None
    last_up = closes[-1] > opens[-1]
    last_dn = closes[-1] < opens[-1]

    prior_high = max(highs[-(C["breakout_lookback"] + 1):-1]) if n > C["breakout_lookback"] + 1 else None
    prior_low = min(lows[-(C["breakout_lookback"] + 1):-1]) if n > C["breakout_lookback"] + 1 else None

    trend_up = (ema_f is not None and ema_s is not None and ema_f > ema_s and price >= ema_s)
    trend_dn = (ema_f is not None and ema_s is not None and ema_f < ema_s and price <= ema_s)

    # ---- session gate -------------------------------------------------
    session_block = None
    mod = None
    if not C["ignore_session"]:
        mod = _candle_minute_of_day(last_t, C["session_tz_offset_min"])
        if mod is not None:
            s_start = _parse_hhmm(C["session_start"], 9 * 60 + 20) + int(C["skip_open_min"])
            s_end = _parse_hhmm(C["session_end"], 15 * 60 + 5) - int(C["skip_close_min"])
            if mod < s_start:
                session_block = f"FACT: before scalp session window ({mod // 60:02d}:{mod % 60:02d} < {s_start // 60:02d}:{s_start % 60:02d})"
            elif mod > s_end:
                session_block = f"FACT: after scalp session window ({mod // 60:02d}:{mod % 60:02d} > {s_end // 60:02d}:{s_end % 60:02d})"

    # ---- evidence scoring (same idiom as signal_engine) --------------
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

    add(ema_f > ema_s if (ema_f is not None and ema_s is not None) else None, 1.0, "EMA_fast>EMA_slow")
    add(price > vwap_v if vwap_v is not None else None, 0.9, "price>VWAP")
    add(roc > 0 if roc is not None else None, 0.8, "positive ROC")
    add(macd_v > 0 if macd_v is not None else None, 0.5, "MACD>0")
    add(vol_expansion if vol_avg is not None else None, 0.7, "volume expansion")

    p_up = 1 / (1 + math.exp(-ev))
    prob_up_pct = _round(p_up * 100, 1)
    prob_dn_pct = _round((1 - p_up) * 100, 1)
    confidence = _round(min(100, abs(ev) / 3.9 * 100), 1)

    regime = "SCALP_TREND_UP" if trend_up else ("SCALP_TREND_DOWN" if trend_dn else "SCALP_RANGE")
    if atr_pct is not None and atr_pct > C["max_atr_pct"]:
        regime = "SCALP_HIGH_VOL"

    # ---- setup detection (deterministic, priority order) -------------
    setup = None
    direction = "NONE"

    def vwap_reclaim():
        if None in (vwap_v, vwap_prev):
            return None
        if closes[-2] < vwap_prev and price > vwap_v and (ema_f is None or ema_s is None or ema_f >= ema_s) and (roc is None or roc >= 0):
            return "BUY"
        if closes[-2] > vwap_prev and price < vwap_v and (ema_f is None or ema_s is None or ema_f <= ema_s) and (roc is None or roc <= 0):
            return "SELL"
        return None

    def ema_pullback():
        if rsi_v is None or rsi_prev is None:
            return None
        if trend_up and rsi_prev <= C["rsi_buy_pullback"] and rsi_v > rsi_prev and last_up and (ema_f is None or price >= ema_f):
            return "BUY"
        if trend_dn and rsi_prev >= C["rsi_sell_pullback"] and rsi_v < rsi_prev and last_dn and (ema_f is None or price <= ema_f):
            return "SELL"
        return None

    def momentum_break():
        if prior_high is None or prior_low is None:
            return None
        strong = (roc is not None and abs(roc) >= C["roc_min_pct"])
        if price > prior_high and strong and roc > 0 and vol_expansion and (vwap_v is None or price >= vwap_v):
            return "BUY"
        if price < prior_low and strong and roc < 0 and vol_expansion and (vwap_v is None or price <= vwap_v):
            return "SELL"
        return None

    detectors = {"VWAP_RECLAIM": vwap_reclaim, "EMA_PULLBACK": ema_pullback, "MOMENTUM_BREAK": momentum_break}
    for name in _SETUP_PRIORITY:
        d = detectors[name]()
        if d in ("BUY", "SELL"):
            setup, direction = name, d
            break

    prob_dir = prob_up_pct if direction == "BUY" else (prob_dn_pct if direction == "SELL" else None)

    # ---- ticks -----------------------------------------------------
    entry = price
    t1 = t2 = final_t = sl = be = trail = rr = tick_target = tick_stop = None
    if atr_v is not None and atr_v > 0 and direction in ("BUY", "SELL"):
        tick_target = _round(C["atr_target_mult"] * atr_v)
        tick_stop = _round(C["atr_stop_mult"] * atr_v)
        trail = _round(C["atr_trail_mult"] * atr_v)
        be = _round(entry)
        if direction == "BUY":
            sl = _round(entry - C["atr_stop_mult"] * atr_v)
            t1 = _round(entry + C["atr_target_mult"] * atr_v)
            t2 = _round(entry + C["atr_target2_mult"] * atr_v)
            final_t = t2
            rr = _round((t1 - entry) / (entry - sl)) if (entry - sl) > 0 else None
        else:
            sl = _round(entry + C["atr_stop_mult"] * atr_v)
            t1 = _round(entry - C["atr_target_mult"] * atr_v)
            t2 = _round(entry - C["atr_target2_mult"] * atr_v)
            final_t = t2
            rr = _round((entry - t1) / (sl - entry)) if (sl - entry) > 0 else None

    facts = {
        "price": price, "staleSec": stale_sec, "candleCount": n,
        "prior_high": _round(prior_high), "prior_low": _round(prior_low),
        "candle_minute_of_day": mod, "last_candle_up": last_up,
    }
    calculations = {
        "ema_fast": _round(ema_f), "ema_slow": _round(ema_s),
        "rsi": _round(rsi_v, 1), "rsi_prev": _round(rsi_prev, 1),
        "atr": _round(atr_v), "atr_pct": _round(atr_pct, 3) if atr_pct is not None else None,
        "vwap": _round(vwap_v), "macd": _round(macd_v), "roc_pct": _round(roc, 3) if roc is not None else None,
        "vol_now": _round(vol_now), "vol_avg": _round(vol_avg), "vol_expansion": vol_expansion,
        "evidence": _round(ev, 2), "trend_up": trend_up, "trend_down": trend_dn,
    }

    # ---- NO-TRADE reasons ---------------------------------------
    nogo = []
    if session_block:
        nogo.append(session_block)
    if stale_sec is not None and stale_sec > C["max_stale_sec"]:
        nogo.append(f"FACT: data stale ({stale_sec}s > {int(C['max_stale_sec'])}s) — scalps need fresh ticks")
    if atr_v is None:
        nogo.append("FACT: ATR unavailable")
    elif atr_pct is not None and atr_pct < C["min_atr_pct"]:
        nogo.append(f"FACT: tape too quiet — ATR {_round(atr_pct,3)}% < {C['min_atr_pct']}% (no scalp edge)")
    elif atr_pct is not None and atr_pct > C["max_atr_pct"]:
        nogo.append(f"FACT: tape too wild — ATR {_round(atr_pct,3)}% > {C['max_atr_pct']}% (scalp risk uncontrolled)")
    if setup is None:
        nogo.append("CALC: no scalp setup (VWAP reclaim / EMA pullback / momentum break) present")
    if direction in ("BUY", "SELL"):
        if (direction == "BUY" and ev < 0) or (direction == "SELL" and ev > 0):
            nogo.append(f"CALC: evidence ({_round(ev,2)}) contradicts {setup} {direction} bias")
        if prob_dir is None or prob_dir < C["prob_min"]:
            nogo.append(f"CALC: probability {prob_dir}% < threshold {C['prob_min']}%")
        if rr is None or rr < C["rr_min"]:
            nogo.append(f"CALC: risk/reward {rr} < minimum {C['rr_min']}")
    spread = _num((inp.get("instrument_meta") or {}).get("spread_pct"))
    if spread is not None and spread > C["max_spread_pct"]:
        nogo.append(f"FACT: spread {spread}% > {C['max_spread_pct']}% — scalp eaten by cost")

    if nogo:
        return base_out("NO_TRADE", {
            "setup": setup,
            "direction": "NONE",
            "market_regime": regime,
            "atr_pct": _round(atr_pct, 3) if atr_pct is not None else None,
            "probability": prob_dir,
            "confidence": confidence,
            "risk_reward": rr,
            "reason": nogo + reasons,
            "facts": facts, "calculations": calculations,
        })

    return base_out("TRADE", {
        "setup": setup,
        "direction": direction,
        "entry_zone": {
            "low": _round(min(entry, entry - atr_v * 0.05)),
            "high": _round(max(entry, entry + atr_v * 0.05)),
            "ref": _round(entry),
        },
        "target_1": t1, "target_2": t2, "final_target": final_t,
        "stop_loss": sl, "break_even": be, "trailing_stop": trail,
        "tick_target": tick_target, "tick_stop": tick_stop,
        "max_hold_sec": C["max_hold_sec"],
        "probability": prob_dir, "confidence": confidence, "risk_reward": rr,
        "market_regime": regime,
        "atr_pct": _round(atr_pct, 3) if atr_pct is not None else None,
        "reason": [f"INTERPRETATION: {setup} → {direction} scalp; exit at +{tick_target} / -{tick_stop} "
                   f"or {int(C['max_hold_sec'])}s, whichever first"] + reasons,
        "invalidation": [
            (f"Close below {sl} (stop)" if direction == "BUY" else f"Close above {sl} (stop)"),
            f"Held longer than {int(C['max_hold_sec'])}s → flatten regardless of price",
            "Loss of VWAP / EMA_fast alignment invalidates the continuation thesis",
        ],
        "facts": facts, "calculations": calculations,
    })
