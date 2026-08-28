"""Turning-Point Engine — bullish/bearish reversal, trend, sideways, false breakout,
determinism, indicator reuse, and deterministic calibration."""
import json
import time

from app.engines.turning_point_engine import run_turning_point_engine


def _c(t, o, h, l, cl, v=1000):
    return [t, round(o, 2), round(h, 2), round(l, 2), round(cl, 2), v]


def _series(closes, wick=0.05, vol=1000, start=None, step=60):
    start = start or (int(time.time()) - len(closes) * step)
    rows, prev = [], closes[0]
    for i, cl in enumerate(closes):
        o = prev
        hi = max(o, cl) + wick
        lo = min(o, cl) - wick
        rows.append(_c(start + i * step, o, hi, lo, cl, vol))
        prev = cl
    return rows


# ---------------------------------------------------------------- 1. bullish reversal
def test_bullish_reversal_at_support():
    px = [120 - i * 0.6 for i in range(34)]          # steep downtrend
    cds = _series(px)
    t = cds[-1][0]
    lowpx = px[-1]
    # capitulation: deep spike down, closes back near the top of a big range, huge volume
    cds.append(_c(t + 60, lowpx, lowpx + 0.4, lowpx - 2.2, lowpx + 0.3, 4200))
    cds.append(_c(t + 120, lowpx + 0.3, lowpx + 1.4, lowpx + 0.1, lowpx + 1.2, 3000))
    out = run_turning_point_engine({"candles": cds, "config": {}})
    assert out["decision"] == "TURN"
    assert out["direction"] == "UP_TURN"
    assert out["p_up"] > 0.55
    assert out["next_high_zone"] is not None
    assert out["trade_ref"]["side"] == "BUY" and out["trade_ref"]["option"] == "CE"
    assert out["swing_low_zone"]["probability"] >= out["swing_high_zone"]["probability"]


# ---------------------------------------------------------------- 2. bearish reversal
def test_bearish_reversal_at_resistance():
    px = [80 + i * 0.6 for i in range(34)]           # steep uptrend
    cds = _series(px)
    t = cds[-1][0]
    hipx = px[-1]
    cds.append(_c(t + 60, hipx, hipx + 2.2, hipx - 0.4, hipx - 0.3, 4200))   # blow-off top
    cds.append(_c(t + 120, hipx - 0.3, hipx - 0.1, hipx - 1.4, hipx - 1.2, 3000))
    out = run_turning_point_engine({"candles": cds, "config": {}})
    assert out["direction"] == "DOWN_TURN"
    assert out["p_down"] > 0.55
    assert out["trade_ref"]["side"] == "SELL" and out["trade_ref"]["option"] == "PE"
    assert out["feature_scores"]["stretch"] < 0        # stretched up -> expect down


# ---------------------------------------------------------------- 3. clean trend (no turn)
def test_clean_trend_is_no_turn_or_low_confidence():
    px = [100 + i * 0.25 for i in range(50)]          # smooth uptrend, no wick, no accel change
    out = run_turning_point_engine({"candles": _series(px, wick=0.02), "config": {}})
    assert out["direction"] == "NO_TURN" or not out["high_confidence"]
    assert abs(out["turn"]) < out["calibration"]["k"]  # sanity: not an extreme turn


# ---------------------------------------------------------------- 4. sideways / range
def test_sideways_midband_is_no_turn():
    px = [100 + (2.0 if i % 2 else -2.0) for i in range(40)]
    px[-1] = 100.0                                    # sitting mid-band
    out = run_turning_point_engine({"candles": _series(px), "config": {}})
    assert out["direction"] == "NO_TURN"
    assert not out["high_confidence"]


# ---------------------------------------------------------------- 5. false breakout
def test_false_breakout_flags_down_turn():
    px = [100 + (i % 6) * 0.3 for i in range(32)]     # choppy range ~100-101.5
    cds = _series(px)
    hi = max(r[2] for r in cds)
    t = cds[-1][0]
    # poke just above the range high, then slam back below with a big upper wick, thin volume
    cds.append(_c(t + 60, hi - 0.2, hi + 0.35, hi - 0.4, hi - 0.5, 400))
    out = run_turning_point_engine({"candles": cds, "config": {}})
    assert out["direction"] == "DOWN_TURN"
    assert out["feature_scores"]["wick"] < 0
    assert out["feature_scores"]["sr"] <= 0           # was pressed into resistance


# ---------------------------------------------------------------- 6. determinism
def test_deterministic():
    px = [110 - i * 0.4 for i in range(35)] + [95.5, 96.8]
    cds = _series(px)
    a = run_turning_point_engine({"candles": cds, "config": {}})
    b = run_turning_point_engine({"candles": cds, "config": {}})
    assert a["turn"] == b["turn"] and a["p_up"] == b["p_up"]
    assert a["feature_scores"] == b["feature_scores"]
    assert a["confidence"] == b["confidence"]


# ---------------------------------------------------------------- 7. reuse signal_calc
def test_signal_calc_reuse_matches_recompute():
    from app.engines.signal_engine import run_signal_engine
    px = [90 + i * 0.5 for i in range(34)] + [107.2, 106.0]
    cds = _series(px)
    sig = run_signal_engine({"candles": cds, "config": {}})
    from_scratch = run_turning_point_engine({"candles": cds, "config": {}})
    with_calc = run_turning_point_engine({"candles": cds, "signal_calc": sig["calculations"],
                                          "config": {}})
    # indicator-derived scores must match within rounding
    for k in ("stretch", "rsi", "band", "mom"):
        assert abs(from_scratch["feature_scores"][k] - with_calc["feature_scores"][k]) < 0.05


# ---------------------------------------------------------------- 8. calibration (closed-form)
def test_recalibrate_moves_k_and_bounds_weights(fresh_db):
    from app import tp_calibration
    db = fresh_db
    feats = list(tp_calibration._SEED["weights"])
    # 120 resolved rows where a positive `turn` correlates with a positive move,
    # and the "stretch" feature carries the signal
    with db.db() as conn:
        for i in range(120):
            turn = 0.4 if i % 2 else -0.4
            signed = 1.2 if turn > 0 else -1.1
            fs = {f: 0.0 for f in feats}
            fs["stretch"] = 0.8 if turn > 0 else -0.8
            conn.execute(
                "INSERT INTO tp_predictions (ts,symbol,timeframe,direction,turn,raw,p_up,confidence,"
                "close_at_pred,atr_at_pred,horizon_bars,resolved,outcome,signed_outcome,feature_scores) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("2026-01-01T00:00:00+00:00", "X", "5m",
                 "UP_TURN" if turn > 0 else "DOWN_TURN", turn, turn, 0.7, 70,
                 100.0, 1.0, 6, 1, "DIRECTION_HIT", signed, json.dumps(fs)))
    cal = tp_calibration.recalibrate()
    assert cal["resolved_n"] == 120
    assert tp_calibration._K_LO <= cal["k"] <= tp_calibration._K_HI
    assert cal["k"] > 0                                   # positive turn -> positive move
    w = cal["weights"]
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert all(tp_calibration._W_LO - 1e-9 <= v <= tp_calibration._W_HI + 1e-9 for v in w.values())
    assert w["stretch"] >= tp_calibration._SEED["weights"]["stretch"]   # signal-carrying feature up-weighted


def test_calibration_load_defaults(fresh_db):
    from app import tp_calibration
    cal = tp_calibration.load()
    assert cal["k"] == 3.2 and cal["b"] == 0.0
    assert abs(sum(cal["weights"].values()) - 1.0) < 1e-6
    assert cal["resolved_n"] == 0
