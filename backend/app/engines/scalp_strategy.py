"""
Autonomous decision engine (spec-13) — composes the P1-P4 engines into a single
signal, strongly NO-TRADE-biased.

    market state -> S/R (P1) -> 4-state + false-breakout (P2)
                 -> regime + MTF (P3) -> CE/PE legs + quality + EV (P4)
                 -> calibrated probability -> final decision

`decide_from_context()` is what the replay harness and (P7) the live runner call.
It is pure given (bars_by_tf, chain, calib): no I/O, no look-ahead.
Returns a dict shaped for db.scalp_signals + the harness `decide` contract, or
a NO_TRADE / WATCH dict.
"""
from __future__ import annotations

from typing import Optional

from .sr_engine import compute_sr
from .state_classifier import classify, BULLISH
from .regime_mtf import detect_regime, mtf_alignment
from .option_engine import analyse_leg, ce_pe_confirmation, ev_gate, select_option

MODEL_VERSION = "scalp-strategy-v1"

# regimes in which the S/R reversal / breakout playbook is simply not run
_BLOCK_REGIMES = {"UNSTABLE"}


def _num(x):
    try:
        f = float(x)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _score_to_prob(signal_score, calib, *, regime, signal_type):
    """Raw 0-100 signal score -> probability of a winning trade.

    P5 calibration table (regime|signal_type curve -> signal_type -> global);
    a missing or degenerate (k==0,b==0) curve falls back to a conservative
    logistic prior, never to a flat 0.5."""
    import math
    s = max(0.0, min(100.0, float(signal_score or 0))) / 100.0
    prior = 1.0 / (1.0 + math.exp(-(2.6 * (s - 0.55))))
    curve = None
    if isinstance(calib, dict):
        curves = calib.get("curves") or {}
        curve = (curves.get(f"{regime}|{signal_type}") or curves.get(f"*|{signal_type}")
                 or calib.get("global"))
    if not curve or (curve.get("k", 0.0) == 0.0 and curve.get("b", 0.0) == 0.0):
        return prior
    return 1.0 / (1.0 + math.exp(-(curve["k"] * (s - 0.5) + curve.get("b", 0.0))))


def _confidence(prob, false_verdict, mtf_conflict):
    if false_verdict == "LIKELY_FALSE" or mtf_conflict:
        return "LOW"
    if prob >= 0.66:
        return "HIGH"
    if prob >= 0.56:
        return "MEDIUM"
    return "LOW"


def _plan_from_leg(sel, direction, cfg):
    """Entry / SL / T1 / T2 / trail from the selected option leg's own S/R+ATR."""
    entry = _num(sel.get("ltp")) or 0.0
    sr = sel.get("sr") or {}
    atr = _num(sr.get("atr")) or max(1.0, entry * 0.02)
    sup = (sr.get("support") or {}).get("level")
    res = (sr.get("resistance") or {}).get("level")
    sl_mult = cfg.get("sl_atr", 1.1)
    t1_mult = cfg.get("t1_atr", 1.7)
    t2_mult = cfg.get("t2_atr", 2.6)
    # long premium either way (BUY CE or BUY PE): SL below entry, targets above
    sl = entry - sl_mult * atr
    if sup is not None and sup < entry:
        sl = max(sl, sup - 0.15 * atr)            # respect the leg's own support
    t1 = entry + t1_mult * atr
    t2 = entry + t2_mult * atr
    if res is not None and res > entry:
        t1 = min(t1, res)                          # don't target through the leg's resistance
    return {
        "entry": round(entry, 2), "stop_loss": round(max(0.05, sl), 2),
        "target_1": round(t1, 2), "target_2": round(t2, 2),
        "trailing_stop": round(cfg.get("trail_atr", 0.9) * atr, 2),
        "max_hold_sec": cfg.get("max_hold_sec", 1500),
    }


def decide_from_context(bars_by_tf: dict, chain: list | None, *,
                        atm: float | None = None, calib: dict | None = None,
                        avg_win: float | None = None, avg_loss: float | None = None,
                        leg_bars_fn=None, config: dict | None = None) -> dict:
    """leg_bars_fn(strike, opt_type) -> {tf: bars} for that option's own candles.
    If None, per-leg analysis runs on the index bars (degraded)."""
    cfg = config or {}
    out_none = lambda why, extra=None: {**({"decision": "NO_TRADE", "signal_type": "NONE",
                                            "direction": "NONE", "reason": why,
                                            "model_version": MODEL_VERSION}), **(extra or {})}

    sr = compute_sr(bars_by_tf, chain=chain, mode="index", config=cfg.get("sr") or {})
    if sr.get("status") != "OK":
        return out_none("S/R unavailable")
    reg = detect_regime(bars_by_tf, config=cfg.get("regime") or {})
    mtf = mtf_alignment(bars_by_tf, config=cfg.get("mtf") or {})
    st = classify(bars_by_tf, sr, chain=chain, config=cfg.get("state") or {})

    if st["state"] == "NONE":
        return out_none("no clean state", {"regime": reg["regime"], "state_score": st.get("state_score"),
                                           "mtf_alignment": mtf["alignment"]})
    if reg["regime"] in _BLOCK_REGIMES:
        return out_none(f"regime {reg['regime']} blocks the S/R playbook",
                        {"regime": reg["regime"], "signal_type": st["state"]})

    direction = st["direction"]                    # BULLISH | BEARISH
    want = "CE" if st["state"] in BULLISH else "PE"

    # MTF gate: a strong opposing HTF read blocks; a conflict caps confidence
    aligned = (direction == "BULLISH" and mtf["alignment"] > 8) or \
              (direction == "BEARISH" and mtf["alignment"] < -8) or abs(mtf["alignment"]) <= 12
    if mtf["htf_dominant"] and not aligned:
        return out_none("MTF: strong opposing higher-timeframe structure",
                        {"regime": reg["regime"], "signal_type": st["state"],
                         "mtf_alignment": mtf["alignment"]})

    # --- CE/PE independent reads on their own candles ---
    price = sr["price"]
    anchor = st["anchor"]["level"]
    index_move_pts = abs(price - anchor) + (sr["atr"] or 0)   # rough expected follow-through
    step = cfg.get("strike_step", 50.0)
    base = round(price / step) * step
    cand_strikes = [base + i * step for i in range(-cfg.get("strike_window", 2), cfg.get("strike_window", 2) + 1)]

    def _leg_row(strike, ot):
        for r in chain or []:
            if r.get("strike") == strike and r.get(ot.lower()):
                # the chain's ce/pe sub-dicts carry no strike -> inject it
                return {**r[ot.lower()], "strike": r["strike"]}
        return None

    def _bars_for(strike, ot):
        if leg_bars_fn:
            try:
                b = leg_bars_fn(strike, ot)
                if b and (b.get("5m") or b.get("3m")):
                    return b
            except Exception:
                pass
        return bars_by_tf                          # degraded: index bars

    ce_row = _leg_row(atm or base, "CE")
    pe_row = _leg_row(atm or base, "PE")
    ce_a = analyse_leg(_bars_for(atm or base, "CE"), ce_row or {"strike": atm or base},
                       opt_type="CE", index_move_pts=index_move_pts, chain=chain, config=cfg.get("opt") or {}) if ce_row else None
    pe_a = analyse_leg(_bars_for(atm or base, "PE"), pe_row or {"strike": atm or base},
                       opt_type="PE", index_move_pts=index_move_pts, chain=chain, config=cfg.get("opt") or {}) if pe_row else None
    conf = ce_pe_confirmation(direction, ce_a, pe_a)
    if conf["agreement"] in ("CONFLICT", "OPPOSING"):
        return out_none(f"CE/PE confirmation {conf['agreement']}",
                        {"regime": reg["regime"], "signal_type": st["state"],
                         "mtf_alignment": mtf["alignment"], "ce_pe": conf})

    # --- select the best contract on the wanted side ---
    cands = []
    for k in cand_strikes:
        row = _leg_row(k, want)
        if not row or _num(row.get("ltp")) in (None, 0.0):
            continue
        cands.append(analyse_leg(_bars_for(k, want), row, opt_type=want, index_move_pts=index_move_pts,
                                 chain=chain, config=cfg.get("opt") or {}, light=True))
    sel = select_option(cands, direction, atm=float(atm or base), config=cfg.get("opt") or {})
    if not sel:
        return out_none("no tradeable contract on the wanted side",
                        {"regime": reg["regime"], "signal_type": st["state"]})
    if sel["final_quality"] < cfg.get("min_option_quality", 45.0):
        return out_none(f"option quality {sel['final_quality']} < min",
                        {"regime": reg["regime"], "signal_type": st["state"], "option_quality": sel["final_quality"]})

    sel_full = analyse_leg(_bars_for(sel["strike"], want), _leg_row(sel["strike"], want) or {"strike": sel["strike"]},
                           opt_type=want, index_move_pts=index_move_pts, chain=chain, config=cfg.get("opt") or {})
    sel = {**sel, "sr": sel_full.get("sr"), "quality_score": sel_full.get("quality_score", sel["quality_score"])}
    plan = _plan_from_leg(sel, direction, cfg)

    # --- calibrated probability + EV gate ---
    # blend the state score with the option quality + MTF magnitude
    blended = 0.62 * st["state_score"] + 0.24 * sel["final_quality"] + 0.14 * min(100.0, mtf["magnitude"] + 40)
    prob = _score_to_prob(blended, calib, regime=reg["regime"], signal_type=st["state"])
    gate = ev_gate(prob, plan["entry"], plan["stop_loss"], plan["target_1"],
                   avg_win=avg_win, avg_loss=avg_loss, config=cfg.get("ev") or {})
    if not gate["pass"]:
        return out_none(f"EV gate: {gate['reason']}",
                        {"regime": reg["regime"], "signal_type": st["state"],
                         "probability": round(prob, 4), "ev": gate["ev"], "rr": gate["rr"],
                         "option_quality": sel["final_quality"]})

    confidence = _confidence(prob, st["false_risk"]["verdict"], mtf["conflict"])
    if confidence == "LOW" and cfg.get("require_min_confidence", "MEDIUM") != "LOW":
        return {"decision": "WATCH", "signal_type": st["state"], "direction": direction,
                "reason": "confidence LOW -> watch only", "probability": round(prob, 4),
                "regime": reg["regime"], "state_score": st["state_score"],
                "mtf_alignment": mtf["alignment"], "model_version": MODEL_VERSION}

    return {
        "decision": "BUY_CE" if want == "CE" else "BUY_PE",
        "signal_type": st["state"], "direction": direction,
        "strike": sel["strike"], "token": sel.get("token"),
        "tradingsymbol": sel.get("tradingsymbol"), "expiry": sel.get("expiry"),
        "entry": plan["entry"], "stop_loss": plan["stop_loss"],
        "target_1": plan["target_1"], "target_2": plan["target_2"],
        "trailing_stop": plan["trailing_stop"], "max_hold_sec": plan["max_hold_sec"],
        "signal_score": round(blended, 1), "probability": round(prob, 4),
        "confidence": confidence, "ev": gate["ev"], "rr": gate["rr"],
        "regime": reg["regime"], "mtf_alignment": mtf["alignment"],
        "support": (sr.get("support") or {}).get("level"),
        "resistance": (sr.get("resistance") or {}).get("level"),
        "support_strength": sr.get("support_strength"),
        "resistance_strength": sr.get("resistance_strength"),
        "sr_level": st["anchor"]["level"], "sr_side": st["anchor"]["side"],
        "atr": sr.get("atr"), "vwap": sr.get("vwap"),
        "momentum": st.get("roc_pct"),
        "component_scores": {**st["components"],
                             "option_quality": sel["final_quality"] / 100.0,
                             "mtf": round(mtf["magnitude"] / 100.0, 3),
                             "regime_conf": reg["confidence"],
                             "false_risk": st["false_risk"]["score"] / 100.0},
        "false_risk": st["false_risk"]["verdict"],
        "ce_pe": conf, "calib_version": (calib or {}).get("version"),
        "reason": " | ".join(st["reason"][:2] + [f"opt_q {sel['final_quality']}",
                                                 f"p {round(prob, 3)}", f"ev {gate['ev_r']}R"]),
        "model_version": MODEL_VERSION,
    }
