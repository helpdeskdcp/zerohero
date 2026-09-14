"""
Orchestrator -- produces the section-25 JSON signal contract.

INDEX-FIRST, as redirected mid-build: direction/probability/confidence/
entry/SL/target are fully computed from the index alone. `option_candidates`
is OPTIONAL -- when omitted (Stage 1: index-only backtesting, no real
option data for the period), `option` in the output is explicitly
"NOT_EVALUATED", never a delta-approximated premium (section 9 explicitly
forbids treating spot-move x delta as a real execution price). Only when
real option-chain candidates ARE supplied (Stage 2, the 10-day real window)
does strike selection / option entry actually run.
"""
from __future__ import annotations

from . import confirmation, indicators, probability as probability_mod, risk, setup_score, strikes, structure, sweep

DEFAULT_CONFIG = {
    "execution_tf": "5m",
    "min_reaction_atr": 0.1,
    "min_probability": 0.55,
    "delta_band": (0.60, 0.70),
    "sl_buffer_atr_mult": 0.1,
}


def _num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _no_trade(reason: str, *, timestamp=None, symbol=None, spot=None, **evidence) -> dict:
    return {
        "timestamp": timestamp, "instrument": symbol, "spot": spot,
        "decision": "NO_TRADE", "reason": reason,
        "passed_rules": [], "failed_rules": [reason],
        "evidence": {k: (v.to_dict() if hasattr(v, "to_dict") else v) for k, v in evidence.items()},
    }


def evaluate(*, symbol: str, bars_by_tf: dict, spot: float, calibration: dict | None = None,
             option_candidates: dict | None = None, config: dict | None = None) -> dict:
    """`bars_by_tf`: {"5m": [...], "15m": [...], "30m": [...], "1h": [...],
    "4h": [...], "1d": [...]} -- whichever the caller can supply, ALL
    already truncated to <= the evaluation timestamp T. `option_candidates`:
    optional {"CE": [...], "PE": [...]} raw option-chain legs (Stage 2 only).
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    exec_tf = cfg["execution_tf"]
    exec_bars = bars_by_tf.get(exec_tf) or []
    timestamp = exec_bars[-1].get("t") if exec_bars else None

    if len(exec_bars) < 30:
        return _no_trade("insufficient execution-timeframe history", timestamp=timestamp, symbol=symbol, spot=spot)

    session = structure._session_date(exec_bars[-1])
    pdh = structure.pdh_pdl(exec_bars, as_of_session=session)
    pts = structure.swings(exec_bars)
    eq_levels = structure.equal_levels(pts)
    levels = []
    if pdh.status == "OK":
        levels += [{"price": pdh.prev_high, "source": "PDH"}, {"price": pdh.prev_low, "source": "PDL"}]
    levels += [{"price": el.level, "source": el.kind} for el in eq_levels]

    ind = indicators.snapshot(exec_bars)
    atr = ind.get("atr14")
    htf = structure.htf_bias(bars_by_tf)

    if not levels:
        return _no_trade("no PDH/PDL or equal-level liquidity identified", timestamp=timestamp,
                         symbol=symbol, spot=spot, htf=htf)

    sw = sweep.detect_sweep(exec_bars, levels, atr=atr, min_reaction_atr=cfg["min_reaction_atr"])
    if sw is None:
        return _no_trade("no confirmed liquidity sweep", timestamp=timestamp, symbol=symbol, spot=spot, htf=htf)

    direction = "BULLISH" if sw.kind == sweep.LOWER_SWEEP else "BEARISH"
    conf = confirmation.evaluate(exec_bars, pts, atr=atr)
    if conf.structure_type == "NONE":
        return _no_trade("no CHoCH/BOS after the sweep", timestamp=timestamp, symbol=symbol, spot=spot,
                         sweep=sw, htf=htf)
    if conf.structure_direction != direction:
        return _no_trade("structure break direction disagrees with sweep direction", timestamp=timestamp,
                         symbol=symbol, spot=spot, sweep=sw, htf=htf, structure=conf)
    if not conf.secondary_confirmed:
        return _no_trade("no secondary confirmation (CISD/FVG/OrderBlock)", timestamp=timestamp,
                         symbol=symbol, spot=spot, sweep=sw, htf=htf, structure=conf)

    setup = setup_score.compute(direction=direction, structure_direction=conf.structure_direction,
                                secondary_confirmed=conf.secondary_confirmed, htf_bias=htf["bias"],
                                indicators_snapshot=ind)

    entry = exec_bars[-1]["c"]
    buffer_pts = (atr * cfg["sl_buffer_atr_mult"]) if atr else entry * 0.0005
    plan = risk.build_plan(direction=direction, entry=entry, sweep_extreme=sw.sweep_extreme, buffer_pts=buffer_pts)
    if plan.status != "OK":
        return _no_trade(plan.reason, timestamp=timestamp, symbol=symbol, spot=spot,
                         sweep=sw, htf=htf, structure=conf, setup=setup)

    p_dir = probability_mod.predict_directional_probability(
        calibration or {}, setup.score_0_100, regime=htf["bias"], signal_type=sw.kind)
    probs = probability_mod.three_way(p_dir, direction)
    conf_score = probability_mod.confidence_score(
        setup_passed_checks=setup.passed_checks, setup_max_checks=setup.max_checks,
        sweep_reaction_atr_ratio=(sw.reaction / atr if atr else None), htf_bias_score=htf["score"])

    if p_dir < cfg["min_probability"]:
        return _no_trade(f"probability {round(p_dir, 3)} < threshold {cfg['min_probability']}",
                         timestamp=timestamp, symbol=symbol, spot=spot, sweep=sw, htf=htf,
                         structure=conf, setup=setup, probability=probs)

    option_type = "CE" if direction == "BULLISH" else "PE"
    option_block = {"type": option_type, "status": "NOT_EVALUATED",
                    "note": "no option_candidates supplied -- index-only (Stage 1) evaluation"}
    if option_candidates:
        ranked = strikes.rank_strikes(option_candidates.get(option_type) or [], spot=spot,
                                      option_type=option_type, delta_band=cfg["delta_band"])
        eligible = [r for r in ranked if r.status == "ELIGIBLE"]
        if not eligible:
            return _no_trade("no eligible ITM strike", timestamp=timestamp, symbol=symbol, spot=spot,
                             sweep=sw, htf=htf, structure=conf, setup=setup, probability=probs)
        best = eligible[0]
        option_block = {"type": option_type, "status": "OK", "strike": best.strike,
                        "ltp": _num(best.raw.get("ltp")), "delta": _num(best.raw.get("delta")),
                        "oi": _num(best.raw.get("oi")), "volume": _num(best.raw.get("volume")),
                        "score": best.score, "candidates": [c.to_dict() for c in ranked[:3]]}

    return {
        "timestamp": timestamp, "instrument": symbol, "spot": spot,
        "market_regime": htf["bias"], "htf_bias": htf,
        "liquidity_sweep": sw.to_dict(),
        "structure": {"type": conf.structure_type, "direction": conf.structure_direction},
        "confirmation": {"cisd": conf.cisd_confirmed, "fvg": conf.fvg_confirmed,
                         "order_block": conf.order_block_confirmed, "candle_closed": True},
        "probability": probs.to_dict(), "confidence": conf_score,
        "setup_score": setup.to_dict(),
        "decision": "BUY_CE" if direction == "BULLISH" else "BUY_PE",
        "option": option_block,
        "entry": plan.entry, "stop_loss": plan.stop_loss, "target_1": plan.target_1,
        "risk_amount": plan.risk_distance, "reward_amount": plan.reward_distance, "rr": plan.rr,
        "passed_rules": ["SWEEP_CONFIRMED", "STRUCTURE_BREAK_ALIGNED", "SECONDARY_CONFIRMATION",
                        "RR_MIN_2R", f"PROBABILITY_ABOVE_{cfg['min_probability']}"],
        "failed_rules": [], "reason": "all mandatory gates passed",
    }
