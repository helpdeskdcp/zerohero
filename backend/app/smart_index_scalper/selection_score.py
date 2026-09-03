"""
INDEX_SELECTION_SCORE (spec section 17).

Weighted 0-100 composite that ranks eligible indices:
    25%  signal quality      (engine confluence_score + confidence)
    20%  OI confluence       (wall strength + interpretation agreement)
    15%  mathematical confluence  (nearest zone evidence + strength)
    15%  liquidity           (ATM OI, normalised across the scan)
    10%  volume              (index volume ratio / chain volume)
    10%  momentum            (|3m momentum| aligned with the engine direction)
     5%  risk/reward         (best RR to T1)

Weights are configurable and NOT calibrated (no backtest — section 26).
"""
from __future__ import annotations

DEFAULT_WEIGHTS = {
    "signal_quality": 0.25, "oi_confluence": 0.20, "math_confluence": 0.15,
    "liquidity": 0.15, "volume": 0.10, "momentum": 0.10, "risk_reward": 0.05,
}


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _clip01(v):
    return max(0.0, min(1.0, v))


def component_scores(*, ctx: dict, engine_out: dict, oi_matrix: dict,
                     liquidity_norm: float) -> dict:
    """liquidity_norm: this index's ATM-OI rank in [0,1] across the current scan."""
    # signal quality
    cs = _f(engine_out.get("confluence_score")) or 0.0
    cf = _f(engine_out.get("confidence")) or 0.0
    sq = _clip01(0.6 * cs / 100.0 + 0.4 * cf / 100.0)

    # OI confluence
    walls = (oi_matrix or {}).get("walls") or {}
    ws = 0.0
    if oi_matrix.get("status") == "OK":
        cr = _f((walls.get("CALL_RESISTANCE_WALL") or {}).get("score")) or 0.0
        pu = _f((walls.get("PUT_SUPPORT_WALL") or {}).get("score")) or 0.0
        ws = _clip01((cr + pu) / 160.0)
    # add interpretation agreement already folded into the engine's oi sub-score
    oi_sub = _f((engine_out.get("score_breakdown") or {}).get("oi", {}).get("raw")) or 0.0
    oic = _clip01(0.6 * ws + 0.4 * oi_sub / 20.0)

    # mathematical confluence
    direction = engine_out.get("direction")
    zone = engine_out.get("nearest_support") if direction == "CE" \
        else engine_out.get("nearest_resistance") if direction == "PE" \
        else (engine_out.get("nearest_support") or engine_out.get("nearest_resistance"))
    z_str = _f((zone or {}).get("strength_score")) or 0.0
    z_ev = _f((zone or {}).get("evidence_count")) or 0.0
    mc = _clip01(0.6 * z_str / 100.0 + 0.4 * min(1.0, z_ev / 4.0))

    # liquidity (pre-normalised by the caller across the scan)
    lq = _clip01(liquidity_norm)

    # volume
    vr = None
    if ctx.get("current_volume") and ctx.get("avg_volume"):
        vr = ctx["current_volume"] / max(1e-9, ctx["avg_volume"])
    chain_vol = sum((_f(r.get("ce_volume")) or 0) + (_f(r.get("pe_volume")) or 0)
                    for r in (ctx.get("chain") or []))
    vol = _clip01((max(0.0, (vr or 1.0) - 1.0)) * 0.7 + (0.3 if chain_vol > 0 else 0.0))

    # momentum aligned with direction
    m3 = _f(ctx.get("mom_3m")) or 0.0
    spot = _f(ctx.get("spot")) or 1.0
    m_norm = min(1.0, abs(m3) / (spot * 0.0015))
    aligned = (direction == "CE" and m3 > 0) or (direction == "PE" and m3 < 0)
    mom = _clip01(m_norm * (1.0 if aligned else 0.3))

    # risk/reward
    rr = engine_out.get("risk_reward")
    rr1 = rr[0] if isinstance(rr, list) and rr else 0.0
    rrs = _clip01((_f(rr1) or 0.0) / 3.0)

    return {"signal_quality": round(sq, 4), "oi_confluence": round(oic, 4),
            "math_confluence": round(mc, 4), "liquidity": round(lq, 4),
            "volume": round(vol, 4), "momentum": round(mom, 4),
            "risk_reward": round(rrs, 4)}


def index_selection_score(components: dict, weights: dict | None = None) -> dict:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    total = 0.0
    breakdown = {}
    for name, wt in w.items():
        c = float(components.get(name, 0.0))
        contrib = c * wt * 100.0
        total += contrib
        breakdown[name] = {"component_0_1": round(c, 3), "weight_pct": round(wt * 100, 1),
                           "contribution": round(contrib, 2)}
    return {"index_selection_score": round(max(0.0, min(100.0, total)), 1),
            "breakdown": breakdown,
            "weights_source": "CONFIGURABLE_DEFAULT (NOT calibrated — needs backtest, section 26)"}


def explain_winner(ranked: list[dict]) -> str:
    """One-paragraph 'why #1 won' vs #2."""
    if not ranked:
        return "no eligible index"
    top = ranked[0]
    if len(ranked) == 1:
        return f"{top['index']} is the only eligible index (score {top['score']})."
    second = ranked[1]
    tb, sb = top["components"], second["components"]
    edges = sorted(((k, tb[k] - sb.get(k, 0)) for k in tb), key=lambda x: -x[1])[:3]
    parts = ", ".join(f"{k.replace('_', ' ')} +{round(d, 2)}" for k, d in edges if d > 0.01)
    return (f"{top['index']} (score {top['score']}) beat {second['index']} "
            f"(score {second['score']}) mainly on: {parts or 'a marginal overall edge'}. "
            f"Signal: {top['signal_type']} {top['direction']}, confidence {top['confidence']}.")
