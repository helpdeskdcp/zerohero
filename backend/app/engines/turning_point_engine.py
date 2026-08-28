"""
AI-TURNING-POINT-ENGINE  (deterministic, rule-based, very low latency)

Mathematically identifies high-probability price TURNING ZONES — where the
market may reverse UP, crash DOWN, or print the next High / Low / Swing High /
Swing Low. It is a mean-reversion / exhaustion engine, complementary to the
trend-following signal_engine and scalp_engine.

Design rules:
  * NO network, NO LLM, NO randomness. Same candles in -> same dict out.
  * Reuses signal_engine's indicator math (EMA/RSI/ATR/VWAP/MACD/ADX). If the
    caller passes `signal_calc` (the calculations dict from run_signal_engine on
    the SAME candles) those values are used verbatim — nothing is recomputed.
  * Predicts ZONES (ATR-scaled ranges), never exact guaranteed prices.
  * Output feeds the Signal Engine gate + Risk Engine (entry/SL/target refs).
    It NEVER places an order.
  * Sigmoid coefficients (k, b) and feature weights can be overridden by
    `calibration` (learned closed-form from tp_predictions history).

Feature-score sign convention:  + = bullish turn / expect UP / a LOW forming
                                - = bearish turn / expect DOWN / a HIGH forming
"""
import math
from datetime import datetime, timezone

from .signal_engine import _num, _round, _sma, _ema_series, _rsi, _atr, _macd, _adx, _vwap

MODEL_VERSION = "turning-point-rule-based-v1"

_EPS = 1e-9
_DEFAULT_WEIGHTS = {
    "stretch": 0.20, "rsi": 0.16, "sr": 0.18, "band": 0.12,
    "wick": 0.12, "mom": 0.12, "vol": 0.06, "oi": 0.04,
}


def _clamp(x, lo=-1.0, hi=1.0):
    return lo if x < lo else (hi if x > hi else x)


def _sigmoid(x):
    if x < -60:
        return 0.0
    if x > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def _stdev(arr):
    n = len(arr)
    if n < 2:
        return 0.0
    m = sum(arr) / n
    return math.sqrt(sum((v - m) ** 2 for v in arr) / n)


def _norm_candles(candles):
    out = []
    for k in candles or []:
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
            out.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v})
    return out


def _last_fractal(highs, lows, n):
    """Bill-Williams 5-bar fractal: last CONFIRMED swing high / low (needs 2 bars
    to the right). Returns (swing_high, sh_age, swing_low, sl_age)."""
    sh = sl = None
    sh_age = sl_age = None
    for i in range(n - 3, 1, -1):
        if sh is None and highs[i] >= max(highs[i - 2:i] + highs[i + 1:i + 3]):
            sh, sh_age = highs[i], n - 1 - i
        if sl is None and lows[i] <= min(lows[i - 2:i] + lows[i + 1:i + 3]):
            sl, sl_age = lows[i], n - 1 - i
        if sh is not None and sl is not None:
            break
    return sh, sh_age, sl, sl_age


def _pivots(prev_hlc):
    if not prev_hlc or len(prev_hlc) < 3:
        return None
    H, L, C = (_num(prev_hlc[0]), _num(prev_hlc[1]), _num(prev_hlc[2]))
    if None in (H, L, C):
        return None
    pp = (H + L + C) / 3.0
    return {
        "pp": _round(pp),
        "r1": _round(2 * pp - L), "s1": _round(2 * pp - H),
        "r2": _round(pp + (H - L)), "s2": _round(pp - (H - L)),
        "r3": _round(H + 2 * (pp - L)), "s3": _round(L - 2 * (H - pp)),
    }


def _oi_metrics(chain, spot):
    """Lightweight PCR + max-pain + OI S/R from a chain, or None."""
    rows = []
    for r in chain or []:
        k = _num(r.get("strike"))
        if k is None:
            continue
        rows.append((k, _num(r.get("ce_oi")) or 0.0, _num(r.get("pe_oi")) or 0.0))
    if len(rows) < 3:
        return None
    tot_ce = sum(x[1] for x in rows) or _EPS
    tot_pe = sum(x[2] for x in rows)
    pcr = tot_pe / tot_ce
    oi_support = max(rows, key=lambda x: x[2])[0]
    oi_resistance = max(rows, key=lambda x: x[1])[0]
    best_k, best = None, float("inf")
    for k0, _, _ in rows:
        pain = sum(max(0, k0 - k) * ce + max(0, k - k0) * pe for k, ce, pe in rows)
        if pain < best:
            best, best_k = pain, k0
    return {"pcr": pcr, "oi_support": oi_support, "oi_resistance": oi_resistance, "max_pain": best_k}


def run_turning_point_engine(inp: dict) -> dict:
    inp = inp or {}
    cfg = inp.get("config") or {}
    cal = inp.get("calibration") or {}
    C = {
        "min_candles": int(_num(cfg.get("min_candles")) or 30),
        "k": _num(cal.get("k")) if _num(cal.get("k")) is not None else (_num(cfg.get("k")) or 3.2),
        "b": _num(cal.get("b")) if _num(cal.get("b")) is not None else (_num(cfg.get("b")) or 0.0),
        "tau": _num(cfg.get("tau")) if _num(cfg.get("tau")) is not None else 0.12,
        "tau_hi": _num(cfg.get("tau_hi")) if _num(cfg.get("tau_hi")) is not None else 0.25,
        "conf_min": _num(cfg.get("conf_min")) or 62,
        "div_lookback": int(_num(cfg.get("div_lookback")) or 8),
        "sr_touch_atr": _num(cfg.get("sr_touch_atr")) or 0.75,
        "climax_vr": _num(cfg.get("climax_vr")) or 2.0,
        "climax_range_atr": _num(cfg.get("climax_range_atr")) or 1.2,
        "hw_atr": _num(cfg.get("hw_atr")) or 0.35,
        "hw_pct": _num(cfg.get("hw_pct")) if _num(cfg.get("hw_pct")) is not None else 0.0015,
        "move_k": _num(cfg.get("move_k")) or 2.2,
        "move_band_atr": _num(cfg.get("move_band_atr")) or 1.4,
        "horizon_bars": int(_num(cfg.get("horizon_bars")) or 6),
        "max_stale_sec": _num(cfg.get("max_stale_sec")) or 900,
    }
    weights = dict(_DEFAULT_WEIGHTS)
    weights.update({k: float(v) for k, v in (cal.get("weights") or {}).items()
                    if k in _DEFAULT_WEIGHTS})

    def base(decision, extra=None):
        out = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model_version": MODEL_VERSION,
            "decision": decision,
            "direction": "NO_TURN",
            "turn": 0.0, "raw": 0.0, "g_regime": 1.0,
            "p_up": 0.5, "p_down": 0.5,
            "confidence": 0, "high_confidence": False,
            "up_turn_zone": None, "down_turn_zone": None,
            "next_high_zone": None, "next_low_zone": None,
            "swing_high_zone": None, "swing_low_zone": None,
            "expected_move": {"direction": "FLAT", "pts": 0.0, "pct": 0.0, "band": None},
            "trade_ref": None,
            "feature_scores": {},
            "calibration": {"k": C["k"], "b": C["b"], "weights": weights,
                            "resolved_n": int(_num(cal.get("resolved_n")) or 0)},
            "horizon_bars": C["horizon_bars"],
            "reason": [], "facts": {},
        }
        if extra:
            out.update(extra)
        return out

    norm = _norm_candles(inp.get("candles"))
    n = len(norm)
    if n < C["min_candles"]:
        return base("DATA_UNAVAILABLE", {"reason": [f"FACT: insufficient candles ({n} < {C['min_candles']})"]})

    o = [x["o"] for x in norm]
    highs = [x["h"] for x in norm]
    lows = [x["l"] for x in norm]
    closes = [x["c"] for x in norm]
    vols = [x["v"] for x in norm]
    price = closes[-1]

    # ---- indicators: reuse signal_calc when provided, else compute -------------
    sc = inp.get("signal_calc") or {}
    ema20 = _num(sc.get("ema20")) if sc.get("ema20") is not None else _ema_series(closes, 20)
    ema50 = _num(sc.get("ema50")) if sc.get("ema50") is not None else _ema_series(closes, min(50, n - 1))
    sma20 = _num(sc.get("sma20")) if sc.get("sma20") is not None else _sma(closes, 20)
    rsi14 = _num(sc.get("rsi14")) if sc.get("rsi14") is not None else _rsi(closes, 14)
    if rsi14 is None:
        rsi14 = _num(sc.get("rsi"))          # scalp_engine names it "rsi"
    atr14 = _num(sc.get("atr14")) if sc.get("atr14") is not None else _atr(highs, lows, closes, n, 14)
    if atr14 is None:
        atr14 = _num(sc.get("atr"))
    vwap = _num(sc.get("vwap")) if sc.get("vwap") is not None else _vwap(highs, lows, closes, vols, n)
    macd = _num(sc.get("macd")) if sc.get("macd") is not None else _macd(closes)
    adx_val = _num(sc.get("adx"))
    if adx_val is None:
        a = _adx(highs, lows, closes, n, 14)
        adx_val = a["adx"] if a else None
    structure = sc.get("structure")
    vol_pct = _num(sc.get("volatility_pct"))
    if vol_pct is None and atr14 and price:
        vol_pct = atr14 / price * 100.0

    if not atr14 or atr14 <= 0:
        return base("DATA_UNAVAILABLE", {"reason": ["FACT: ATR unavailable"]})

    # values that are cheap and always local
    sma20 = sma20 if sma20 is not None else _sma(closes, min(20, n))
    std20 = _stdev(closes[-20:]) if n >= 5 else 0.0
    rsi_prev = _rsi(closes[:-1], 14) if n > 16 else rsi14
    macd_prev = _macd(closes[:-1]) if n > 27 else macd
    roc_now = (closes[-1] - closes[-4]) / abs(closes[-4]) * 100.0 if n > 4 and closes[-4] else 0.0
    roc_prev = (closes[-2] - closes[-5]) / abs(closes[-5]) * 100.0 if n > 5 and closes[-5] else 0.0

    # 20-bar swing S/R
    sw = min(20, n)
    resistance = max(highs[-sw:])
    support = min(lows[-sw:])
    swing_high, sh_age, swing_low, sl_age = _last_fractal(highs, lows, n)
    pivots = _pivots(inp.get("prev_session_hlc"))
    oi = _oi_metrics(inp.get("chain"), price)

    # ---- new primitives ------------------------------------------------------
    z = (price - sma20) / max(atr14, _EPS) if sma20 is not None else 0.0
    upper_bb = (sma20 + 2 * std20) if sma20 is not None else price
    lower_bb = (sma20 - 2 * std20) if sma20 is not None else price
    pct_b = (price - lower_bb) / max(upper_bb - lower_bb, _EPS)

    last = norm[-1]
    rng = max(last["h"] - last["l"], _EPS)
    lw = (min(last["o"], last["c"]) - last["l"]) / rng
    uw = (last["h"] - max(last["o"], last["c"])) / rng
    # blend last 3 bars 0.5 / 0.3 / 0.2
    wick_blend = 0.0
    for wgt, bar in zip((0.5, 0.3, 0.2), norm[-1:-4:-1]):
        r = max(bar["h"] - bar["l"], _EPS)
        wick_blend += wgt * (((min(bar["o"], bar["c"]) - bar["l"]) / r)
                             - ((bar["h"] - max(bar["o"], bar["c"])) / r))

    vol_mean = _sma(vols[:-1], min(20, n - 1)) or _EPS
    vr = (vols[-1] / vol_mean) if vol_mean > 0 else 1.0
    is_climax = vr >= C["climax_vr"] and rng >= C["climax_range_atr"] * atr14

    # RSI divergence over the window
    div = 0
    if n > C["div_lookback"] + 16:
        past_close = max(closes[-C["div_lookback"]:-1])
        past_rsi = _rsi(closes[:-C["div_lookback"] + 1], 14)
        if past_rsi is not None:
            if price > past_close and rsi14 is not None and rsi14 < past_rsi - 1:
                div = -1                                  # bearish divergence
            elif closes[-1] < min(closes[-C["div_lookback"]:-1]) and rsi14 > past_rsi + 1:
                div = 1                                   # bullish divergence

    # ---- feature scores S in [-1, 1] --------------------------------------
    S = {}
    S["stretch"] = _clamp(-z / 2.5)

    s_rsi = _clamp(-((rsi14 - 50) / 30)) if rsi14 is not None else 0.0
    if div == -1:
        s_rsi -= 0.4 * (1 - abs(s_rsi))
    elif div == 1:
        s_rsi += 0.4 * (1 - abs(s_rsi))
    S["rsi"] = _clamp(s_rsi)

    S["band"] = _clamp(-((pct_b - 0.5) / 0.5))

    r_levels = [resistance] + [x for x in (
        pivots["r1"] if pivots else None, pivots["r2"] if pivots else None,
        oi["oi_resistance"] if oi else None, swing_high) if x is not None and x > price]
    s_levels = [support] + [x for x in (
        pivots["s1"] if pivots else None, pivots["s2"] if pivots else None,
        oi["oi_support"] if oi else None, swing_low) if x is not None and x < price]
    r_star = min(r_levels) if r_levels else resistance
    s_star = max(s_levels) if s_levels else support
    near_r = _clamp(1 - (r_star - price) / (C["sr_touch_atr"] * atr14), 0, 1)
    near_s = _clamp(1 - (price - s_star) / (C["sr_touch_atr"] * atr14), 0, 1)
    S["sr"] = _clamp(near_s - near_r)

    S["wick"] = _clamp(wick_blend * (1.0 if rng >= 0.5 * atr14 else 0.5))

    macd_slope = (macd - macd_prev) if (macd is not None and macd_prev is not None) else 0.0
    roc_decel = roc_now - roc_prev
    if price > (sma20 or price):                 # up move losing steam -> DOWN turn
        s_mom = -_clamp((max(0.0, -macd_slope) + max(0.0, -roc_decel) * 0.5) / (0.3 * atr14 + _EPS), 0, 1)
    else:                                        # down move losing steam -> UP turn
        s_mom = _clamp((max(0.0, macd_slope) + max(0.0, roc_decel) * 0.5) / (0.3 * atr14 + _EPS), 0, 1)
    S["mom"] = _clamp(s_mom)

    if is_climax:
        # direction of a climax bar = its net REJECTION, not close-vs-open:
        # a down-spike-and-recover (big lower wick) is a bullish capitulation;
        # a blow-off (big upper wick) is a bearish exhaustion.
        net_rej = lw - uw
        if abs(net_rej) < 0.12:
            net_rej = (last["c"] - last["o"])
        S["vol"] = _clamp((1 if net_rej > 0 else -1) * min((vr - 1) / 2, 1))
    else:
        S["vol"] = 0.0

    if oi:
        s_oi = 0.5 * _clamp((oi["pcr"] - 1.0) / 1.0)
        if oi["max_pain"]:
            s_oi += 0.3 * _clamp((oi["max_pain"] - price) / (2 * atr14), -1, 1)
        S["oi"] = _clamp(s_oi)
        active = list(weights)
    else:
        S["oi"] = 0.0
        active = [k for k in weights if k != "oi"]

    # renormalise the active weight set
    wsum = sum(weights[k] for k in active) or _EPS
    w = {k: (weights[k] / wsum if k in active else 0.0) for k in weights}

    raw = sum(w[k] * S[k] for k in S)

    # Trend dampening applies to CONTEXT features only. Genuine exhaustion
    # (wick / volume-climax / RSI-extreme+divergence / band-edge) fires even
    # mid-trend — that is the whole point of a turning-point engine.
    _EXHAUST = ("rsi", "band", "wick", "vol")
    _CONTEXT = ("stretch", "sr", "mom", "oi")
    # a turn needs a TRIGGER, not just an overextended reading — a rejection
    # wick, a volume climax, an RSI divergence, or clear momentum fade.
    has_trigger = (abs(S["wick"]) > 0.25 or S["vol"] != 0.0 or div != 0
                   or abs(S["mom"]) > 0.30)
    raw_ex = sum(w[k] * S[k] for k in _EXHAUST)
    if not has_trigger:
        raw_ex *= 0.6                     # "overextended" but not yet turning
    raw_ctx = sum(w[k] * S[k] for k in _CONTEXT)

    if adx_val is not None:
        g_regime = _clamp(1 - (adx_val - 20) / 30, 0.40, 1)
    else:
        g_regime = 0.75
    is_flat = bool(sc.get("flat_market")) or (vol_pct is not None and vol_pct < 0.03)
    if structure in ("RANGE", "EXPANSION") or (sc.get("market_regime") in ("RANGE", "FLAT", "TRANSITION")):
        g_regime = max(g_regime, 0.9)

    turn = raw_ex + g_regime * raw_ctx

    # climax boost: volume-climax rejection wick at an RSI extreme, all agreeing
    if (is_climax and abs(S["wick"]) > 0.3 and abs(S["rsi"]) > 0.4
            and (S["wick"] > 0) == (S["rsi"] > 0) and (turn > 0) == (S["wick"] > 0)):
        turn *= 1.4
    turn = _clamp(turn, -1.0, 1.0)

    # ---- probability + direction ---------------------------------------
    p_up = round(_sigmoid(C["k"] * turn + C["b"]), 4)
    p_down = round(1 - p_up, 4)
    if turn >= C["tau"]:
        direction = "UP_TURN"
    elif turn <= -C["tau"]:
        direction = "DOWN_TURN"
    else:
        direction = "NO_TURN"

    # ---- confidence ---------------------------------------------------
    nz_w = sum(w[k] for k in S if S[k] != 0.0) or _EPS
    agree_w = sum(w[k] for k in S if S[k] != 0.0 and (S[k] > 0) == (turn > 0))
    agree = agree_w / nz_w
    strength = min(1.0, abs(turn) / 0.45)

    stale_sec = None
    lt = norm[-1]["t"]
    try:
        if isinstance(lt, (int, float)):
            tms = lt if lt > 1e12 else lt * 1000
        else:
            tms = datetime.fromisoformat(str(lt).replace("Z", "+00:00")).timestamp() * 1000
        import time as _t
        stale_sec = round((_t.time() * 1000 - tms) / 1000)
    except Exception:
        stale_sec = None
    data_q = min(1.0, n / 40.0) * (0.5 if (stale_sec and stale_sec > C["max_stale_sec"]) else 1.0)
    confidence = int(round(100 * agree * (strength ** 0.7) * data_q * (g_regime ** 0.3)))
    high_conf = (confidence >= C["conf_min"] and abs(turn) >= C["tau_hi"]
                 and not is_flat and direction != "NO_TURN" and has_trigger)

    # ---- zones (ATR-scaled) ---------------------------------------
    hw = max(C["hw_atr"] * atr14, C["hw_pct"] * price)
    up_turn_zone = down_turn_zone = None
    next_high = next_low = None
    trade_ref = None

    if direction == "UP_TURN":
        up_turn_zone = [_round(price - 0.6 * hw), _round(price + 0.4 * hw)]
        m = 1.0 + 0.5 * p_down
        centre = max(s_star, price - m * atr14) if s_star < price else price - m * atr14
        # (a bullish turn: the *upside* projection to the next high)
        up_m = 1.0 + 0.6 * p_up
        hi_centre = min(r_star, price + up_m * atr14) if r_star > price else price + up_m * atr14
        reach = _clamp(up_m * atr14 / (abs(r_star - price) + _EPS), 0.3, 1)
        next_high = {"zone": [_round(hi_centre - hw), _round(hi_centre + hw)],
                     "probability": _round(p_up * reach, 3)}
        entry = sum(up_turn_zone) / 2
        sl = _round(up_turn_zone[0] - 0.5 * hw)
        t1, t2 = next_high["zone"][0], next_high["zone"][1]
        rr = _round((t1 - entry) / (entry - sl)) if (entry - sl) > 0 else None
        trade_ref = {"side": "BUY", "option": "CE", "entry_ref": _round(entry),
                     "stop_loss": sl, "target_1": _round(t1), "target_2": _round(t2),
                     "risk_reward": rr}

    elif direction == "DOWN_TURN":
        down_turn_zone = [_round(price - 0.4 * hw), _round(price + 0.6 * hw)]
        dn_m = 1.0 + 0.6 * p_down
        lo_centre = max(s_star, price - dn_m * atr14) if s_star < price else price - dn_m * atr14
        reach = _clamp(dn_m * atr14 / (abs(price - s_star) + _EPS), 0.3, 1)
        next_low = {"zone": [_round(lo_centre - hw), _round(lo_centre + hw)],
                    "probability": _round(p_down * reach, 3)}
        entry = sum(down_turn_zone) / 2
        sl = _round(down_turn_zone[1] + 0.5 * hw)
        t1, t2 = next_low["zone"][1], next_low["zone"][0]
        rr = _round((entry - t1) / (sl - entry)) if (sl - entry) > 0 else None
        trade_ref = {"side": "SELL", "option": "PE", "entry_ref": _round(entry),
                     "stop_loss": sl, "target_1": _round(t1), "target_2": _round(t2),
                     "risk_reward": rr}

    # swing zones — always reported (probabilities scale with turn direction)
    sh_bonus = 1.15 if structure == "LOWER_HIGH_LOWER_LOW" else 1.0
    sl_bonus = 1.15 if structure == "HIGHER_HIGH_HIGHER_LOW" else 1.0
    sh_ref = swing_high if swing_high is not None else resistance
    sl_ref = swing_low if swing_low is not None else support
    swing_high_zone = {"zone": [_round(sh_ref - hw), _round(sh_ref + 1.2 * hw)],
                       "probability": _round(min(0.98, p_down * sh_bonus), 3), "age_bars": sh_age}
    swing_low_zone = {"zone": [_round(sl_ref - 1.2 * hw), _round(sl_ref + hw)],
                      "probability": _round(min(0.98, p_up * sl_bonus), 3), "age_bars": sl_age}

    em_pts = _round((p_up - p_down) * abs(turn) * atr14 * C["move_k"])
    em_dir = "UP" if em_pts > 0 else ("DOWN" if em_pts < 0 else "FLAT")
    expected_move = {
        "direction": em_dir, "pts": em_pts,
        "pct": _round(em_pts / price * 100, 3) if price else 0.0,
        "band": [_round(price + em_pts - C["move_band_atr"] * atr14),
                 _round(price + em_pts + C["move_band_atr"] * atr14)],
    }

    reason = [f"turn={_round(turn,3)} raw={_round(raw,3)} g_regime={_round(g_regime,2)} "
              f"p_up={p_up} conf={confidence}"]
    reason += [f"{k}:{_round(S[k],2)}" for k in ("stretch", "rsi", "sr", "band", "wick", "mom", "vol", "oi")]
    if div:
        reason.append("bearish RSI divergence" if div < 0 else "bullish RSI divergence")
    if is_climax:
        reason.append(f"volume climax vr={_round(vr,2)}")

    return base("TURN" if direction != "NO_TURN" else "NO_TURN", {
        "direction": direction,
        "turn": _round(turn, 4), "raw": _round(raw, 4), "g_regime": _round(g_regime, 3),
        "p_up": p_up, "p_down": p_down,
        "confidence": confidence, "high_confidence": high_conf,
        "up_turn_zone": up_turn_zone, "down_turn_zone": down_turn_zone,
        "next_high_zone": next_high, "next_low_zone": next_low,
        "swing_high_zone": swing_high_zone, "swing_low_zone": swing_low_zone,
        "expected_move": expected_move,
        "trade_ref": trade_ref,
        "feature_scores": {k: _round(S[k], 3) for k in S},
        "reason": reason,
        "facts": {
            "price": price, "atr14": _round(atr14), "z": _round(z, 2), "pct_b": _round(pct_b, 3),
            "vr": _round(vr, 2), "rsi14": _round(rsi14, 1) if rsi14 is not None else None,
            "adx": _round(adx_val, 1) if adx_val is not None else None,
            "support": _round(support), "resistance": _round(resistance),
            "s_star": _round(s_star), "r_star": _round(r_star),
            "swing_high": _round(swing_high), "swing_low": _round(swing_low),
            "pivots": pivots, "oi": {k: _round(v) for k, v in oi.items()} if oi else None,
            "stale_sec": stale_sec, "n": n, "divergence": div, "climax": is_climax,
        },
    })
