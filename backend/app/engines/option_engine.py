"""
Independent CE / PE option engines, option-quality selector, and the EV/RR gate
(spec-5, spec-8, spec-9, spec-10).

An option is NOT just the index signal copied onto a leg. Each candidate leg
gets its own read: own S/R (P1 option mode), own trend/momentum, premium-
response validation (does the expected index move actually move the premium?),
theta drag, IV context, and a liquidity proxy. The selector then picks the
single best-quality contract for the wanted direction, and the EV gate can
reject a directionally-correct setup whose expected value is too thin.

Degraded-data aware: greeks are ~40% NULL and tokens/expiry ~90% NULL in the
archive -> every greek-dependent path has a no-greek fallback; nothing is
fabricated.
"""
from __future__ import annotations

from statistics import median
from typing import Optional

from .signal_engine import _atr, _ema_series, _rsi
from .sr_engine import compute_sr

MODEL_VERSION = "option-engine-v1"

_QW = {                     # option-quality component weights (seed)
    "liquidity": 0.24, "translation": 0.22, "delta_fit": 0.16,
    "premium_fit": 0.12, "atm_proximity": 0.12, "own_trend": 0.10, "theta": 0.04,
}


def _num(x):
    try:
        f = float(x)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _closes(bars):
    C, H, L = [], [], []
    for k in bars or []:
        if isinstance(k, (list, tuple)):
            h, l, c = (_num(k[i]) for i in (2, 3, 4))
        else:
            h, l, c = (_num(k.get(x)) for x in ("h", "l", "c"))
        if None in (h, l, c):
            continue
        H.append(h); L.append(l); C.append(c)
    return H, L, C


def _own_trend(bars):
    H, L, C = _closes(bars)
    n = len(C)
    if n < 12:
        return {"dir": "NONE", "rsi": None, "roc_pct": 0.0, "atr": None, "atr_pct": None, "n": n}
    ef, es = _ema_series(C, min(9, n - 1)), _ema_series(C, min(21, n - 1))
    rsi = _rsi(C, 14)
    roc = (C[-1] - C[-4]) / abs(C[-4]) * 100.0 if n >= 4 and C[-4] else 0.0
    atr = _atr(H, L, C, n, min(14, n - 1))
    d = "UP" if (ef and es and C[-1] > ef > es) else ("DOWN" if (ef and es and C[-1] < ef < es) else "NONE")
    return {"dir": d, "rsi": round(rsi, 1) if rsi is not None else None,
            "roc_pct": round(roc, 3), "atr": round(atr, 3) if atr else None,
            "atr_pct": round(atr / C[-1] * 100.0, 3) if (atr and C[-1]) else None, "n": n}


def _translation(leg, opt_type, index_move_pts, own_trend, cfg):
    """Does the expected index move translate into a *tradeable* premium move?
    Scored 0..1 against the larger of a minimum worthwhile move and 1 leg-ATR."""
    min_move = cfg.get("min_premium_move", 8.0)
    leg_atr = own_trend.get("atr") or min_move
    denom = max(min_move, leg_atr)
    delta = _num(leg.get("delta"))
    gamma = _num(leg.get("gamma"))
    if delta is not None:
        eff = abs(delta) * abs(index_move_pts) + 0.5 * (gamma or 0) * index_move_pts ** 2
        return max(0.0, min(1.0, eff / denom)), {
            "method": "greeks", "expected_premium_move": round(eff, 2), "delta": delta}
    # no-greek fallback: leg's own realised responsiveness
    ap = own_trend.get("atr_pct")
    resp = _num(leg.get("chg_pct"))
    score = 0.0
    if ap is not None:
        score += min(1.0, ap / 1.0) * 0.6
    if resp is not None:
        score += min(1.0, abs(resp) / 6.0) * 0.4
    return max(0.0, min(1.0, score)), {"method": "fallback", "atr_pct": ap, "chg_pct": resp}


def analyse_leg(leg_bars_by_tf: dict, leg: dict, *, opt_type: str,
                index_move_pts: float, chain: list | None = None,
                config: dict | None = None) -> dict:
    cfg = config or {}
    ot = str(opt_type).upper()
    own = _own_trend(leg_bars_by_tf.get("5m") or leg_bars_by_tf.get("3m") or [])
    sr = compute_sr(leg_bars_by_tf, mode="option", config=cfg.get("sr") or {})
    trans, trans_dbg = _translation(leg, ot, index_move_pts, own, cfg)

    ltp = _num(leg.get("ltp")) or 0.0
    delta = _num(leg.get("delta"))
    theta = _num(leg.get("theta"))
    iv = _num(leg.get("iv"))
    oi = _num(leg.get("oi")) or 0.0
    vd = _num(leg.get("vol_delta")) or 0.0

    theta_drag = max(0.0, min(1.0, abs(theta) / ltp / 0.5)) if (theta is not None and ltp) else 0.3
    iv_ctx = "UNKNOWN"
    if iv is not None and chain:
        ivs = [_num((r.get(ot.lower()) or {}).get("iv")) for r in chain]
        ivs = [v for v in ivs if v is not None]
        if ivs:
            m = median(ivs)
            iv_ctx = "RICH" if iv > m * 1.12 else ("CHEAP" if iv < m * 0.88 else "FAIR")

    liq = max(0.0, min(1.0, (min(1.0, oi / 300000.0) * 0.5 + min(1.0, vd / 20000.0) * 0.5)))
    delta_fit = 0.5
    if delta is not None:
        a = abs(delta)
        delta_fit = 1.0 if 0.35 <= a <= 0.62 else max(0.0, 1.0 - abs(a - 0.48) / 0.35)
    lo, hi = cfg.get("premium_min", 15.0), cfg.get("premium_max", 400.0)
    premium_fit = 1.0 if lo <= ltp <= hi else (0.3 if ltp else 0.0)

    want_up = ot == "CE"
    own_ok = 1.0 if own["dir"] == ("UP" if want_up else "DOWN") else (0.4 if own["dir"] == "NONE" else 0.0)

    quality = 100.0 * (
        _QW["liquidity"] * liq + _QW["translation"] * trans + _QW["delta_fit"] * delta_fit
        + _QW["premium_fit"] * premium_fit + _QW["own_trend"] * own_ok
        + _QW["theta"] * (1.0 - theta_drag)
        + _QW["atm_proximity"] * 0.5          # filled by the selector with the real value
    )
    confirm = "STRONG" if (trans >= 0.45 and own_ok >= 0.4 and liq >= 0.25) else \
        ("OPPOSING" if own_ok == 0.0 else "WEAK")

    return {
        "opt_type": ot, "ltp": ltp, "strike": _num(leg.get("strike")),
        "token": leg.get("token"), "tradingsymbol": leg.get("tradingsymbol"),
        "expiry": leg.get("expiry"),
        "own_trend": own, "sr": {k: sr.get(k) for k in ("support", "resistance", "support_strength",
                                                        "resistance_strength", "atr", "vwap", "status")},
        "translation_score": round(trans, 3), "translation": trans_dbg,
        "theta_drag": round(theta_drag, 3), "iv_context": iv_ctx,
        "liquidity_score": round(liq, 3), "delta_fit": round(delta_fit, 3),
        "premium_fit": round(premium_fit, 3),
        "quality_score": round(max(0.0, min(100.0, quality)), 1),
        "confirm": confirm, "greeks_available": delta is not None,
        "model_version": MODEL_VERSION,
    }


def ce_pe_confirmation(direction: str, ce: dict | None, pe: dict | None) -> dict:
    """spec-5: index sets structure; CE and PE must independently agree."""
    d = str(direction).upper()
    bull = d in ("BULLISH", "BUY_CE", "UP")
    primary, other = (ce, pe) if bull else (pe, ce)
    p_conf = (primary or {}).get("confirm", "WEAK")
    o_conf = (other or {}).get("confirm", "WEAK")
    if p_conf == "STRONG" and o_conf != "STRONG":
        agreement = "CONFIRMED"
    elif p_conf == "STRONG" and o_conf == "STRONG":
        agreement = "CONFLICT"          # both legs bid -> unclear -> prefer NO_TRADE
    elif p_conf == "OPPOSING":
        agreement = "OPPOSING"
    else:
        agreement = "WEAK"
    return {"primary_side": "CE" if bull else "PE",
            "primary_confirm": p_conf, "other_confirm": o_conf, "agreement": agreement}


def select_option(candidates: list[dict], direction: str, *, atm: float,
                  config: dict | None = None) -> Optional[dict]:
    """spec-9: choose the single best-quality contract, not merely the right side.
    `candidates` = list of analyse_leg() results for the wanted side."""
    cfg = config or {}
    if not candidates:
        return None
    step = cfg.get("strike_step", 50.0)
    scored = []
    for c in candidates:
        strike = _num(c.get("strike"))
        prox = 1.0 - min(1.0, abs((strike or atm) - atm) / (3 * step)) if strike is not None else 0.3
        q = (c["quality_score"] / 100.0) - _QW["atm_proximity"] * 0.5 + _QW["atm_proximity"] * prox
        scored.append((q, prox, c))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    best_q, prox, best = scored[0]
    return {**best, "final_quality": round(max(0.0, min(1.0, best_q)) * 100.0, 1),
            "atm_proximity": round(prox, 3)}


def ev_gate(prob: float, entry: float, stop_loss: float, target_1: float, *,
            avg_win: float | None = None, avg_loss: float | None = None,
            slippage: float = 0.0, cost: float = 0.0, config: dict | None = None) -> dict:
    """spec-10: reject a directionally-correct trade whose EV is too thin."""
    cfg = config or {}
    p = max(0.0, min(1.0, float(prob)))
    risk = max(1e-9, entry - stop_loss)
    reward = target_1 - entry
    rr = round(reward / risk, 3)
    aw = avg_win if avg_win is not None else reward
    al = avg_loss if avg_loss is not None else risk
    ev = round(p * aw - (1.0 - p) * al - slippage - cost, 3)
    ev_r = round(ev / risk, 3)
    min_ev_r = cfg.get("min_ev_r", 0.12)          # EV must be >= 0.12R after costs
    rr_min = cfg.get("rr_min", 1.3)
    passed = ev_r >= min_ev_r and rr >= rr_min and reward > 0 and risk > 0
    return {"ev": ev, "ev_r": ev_r, "rr": rr, "prob": round(p, 4),
            "avg_win": round(aw, 2), "avg_loss": round(al, 2),
            "pass": bool(passed),
            "reason": ("OK" if passed else
                       f"EV {ev_r}R < {min_ev_r}R" if ev_r < min_ev_r else
                       f"RR {rr} < {rr_min}" if rr < rr_min else "bad geometry")}
