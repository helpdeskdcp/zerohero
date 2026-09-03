"""
The 7 sub-scores + weighted CONFLUENCE_SCORE (section 13).

Weights are CONFIGURABLE and NOT statistically calibrated — the engine says so
in every output until a backtest (section 26) exists.
"""
from __future__ import annotations

DEFAULT_WEIGHTS = {
    "mathematical": 0.20,
    "oi": 0.20,
    "price_action": 0.20,
    "volume": 0.10,
    "breakout": 0.10,
    "retest": 0.10,
    "swing": 0.10,
}

SUBSCORE_MAX = {"mathematical": 20, "oi": 20, "price_action": 20, "volume": 10,
                "breakout": 10, "retest": 10, "swing": 10}


def _clip(v, lo, hi):
    return max(lo, min(hi, v))


def mathematical_score(nearest_zone: dict | None, high_conf_zones: list[dict]) -> tuple[float, list[str]]:
    if not nearest_zone:
        return 0.0, ["no confluence zone near price"]
    r = []
    s = min(14.0, nearest_zone["strength_score"] / 100.0 * 14.0)
    r.append(f"nearest zone strength {nearest_zone['strength_score']} ({nearest_zone['evidence_count']} families)")
    if any(z["center"] == nearest_zone["center"] for z in high_conf_zones):
        s += 6.0
        r.append("nearest zone is HIGH-CONFLUENCE (>=3 independent families)")
    return round(_clip(s, 0, 20), 1), r


def oi_score(matrix: dict, direction: str) -> tuple[float, list[str]]:
    if not matrix or matrix.get("status") != "OK":
        return 0.0, ["OI data insufficient"]
    r = []
    s = 0.0
    walls = matrix.get("walls") or {}
    # a supportive wall on the trade side
    if direction == "CE" and walls.get("PUT_SUPPORT_WALL"):
        s += 6.0 * min(1.0, walls["PUT_SUPPORT_WALL"]["score"] / 60.0)
        r.append(f"PUT support wall @ {walls['PUT_SUPPORT_WALL']['strike']}")
    if direction == "PE" and walls.get("CALL_RESISTANCE_WALL"):
        s += 6.0 * min(1.0, walls["CALL_RESISTANCE_WALL"]["score"] / 60.0)
        r.append(f"CALL resistance wall @ {walls['CALL_RESISTANCE_WALL']['strike']}")
    # interpretation agreement near the money
    near = sorted(matrix["rows"], key=lambda x: x.get("battle_score", 0), reverse=True)[:3]
    want = {"CE": ("put_writing", "call_short_covering", "put_long_unwinding"),
            "PE": ("call_writing", "put_short_covering", "call_long_unwinding")}
    # (approx mapping of the standard matrix labels)
    lbl_map = {"fresh_writing": "writing", "short_covering": "short_covering",
               "long_unwinding": "unwinding", "fresh_buying": "buying"}
    agree = 0
    for row in near:
        ce_l = lbl_map.get(row.get("ce_interpretation"))
        pe_l = lbl_map.get(row.get("pe_interpretation"))
        if direction == "CE" and (pe_l == "writing" or ce_l == "short_covering"):
            agree += 1
        if direction == "PE" and (ce_l == "writing" or pe_l == "short_covering"):
            agree += 1
    s += min(8.0, agree * 3.0)
    if agree:
        r.append(f"{agree} near-money strike(s) show OI-action supporting {direction}")
    pcr = matrix.get("pcr")
    if pcr is not None:
        if direction == "CE" and pcr > 1.2:
            s += 3.0; r.append(f"PCR {pcr} > 1.2 (put-heavy => support)")
        if direction == "PE" and pcr < 0.8:
            s += 3.0; r.append(f"PCR {pcr} < 0.8 (call-heavy => resistance)")
    return round(_clip(s, 0, 20), 1), r


def price_action_score(*, candle_signals: list[str], at_zone: bool, direction: str,
                       reversal_candidate: bool) -> tuple[float, list[str]]:
    r = []
    s = 0.0
    if at_zone:
        s += 6.0
        r.append("price interacting with a confluence zone")
    good = {"CE": {"bullish_engulfing", "hammer", "lower_wick", "higher_low", "failed_breakdown"},
            "PE": {"bearish_engulfing", "shooting_star", "upper_wick", "lower_high", "failed_breakout"}}
    hits = [c for c in (candle_signals or []) if c in good.get(direction, set())]
    s += min(9.0, 3.0 * len(hits))
    if hits:
        r.append("candle structure: " + ", ".join(hits))
    if reversal_candidate:
        s += 5.0
        r.append("reversal candidate confirmed at the zone")
    return round(_clip(s, 0, 20), 1), r


def volume_score(*, vol_ratio: float | None, direction_aligned: bool) -> tuple[float, list[str]]:
    if vol_ratio is None:
        return 0.0, ["volume UNAVAILABLE"]
    s = min(7.0, max(0.0, (vol_ratio - 1.0)) * 7.0)
    r = [f"volume {vol_ratio:.2f}x recent average"]
    if direction_aligned and vol_ratio >= 1.2:
        s += 3.0
        r.append("volume expansion aligned with direction")
    return round(_clip(s, 0, 10), 1), r


def breakout_score(state: str | None) -> tuple[float, list[str]]:
    m = {"BREAKOUT_CONFIRMED": (9.0, "confirmed breakout (3m close + volume + no immediate reject)"),
         "BREAKDOWN_CONFIRMED": (9.0, "confirmed breakdown (3m close + volume + retest failure)"),
         "BREAKOUT_WATCH": (4.0, "breakout forming, not confirmed"),
         "BREAKDOWN_WATCH": (4.0, "breakdown forming, not confirmed"),
         "WICK_ONLY": (1.0, "wick-only poke — rejected")}
    v = m.get(state, (0.0, "no breakout event"))
    return round(v[0], 1), [v[1]]


def retest_score(state: str | None) -> tuple[float, list[str]]:
    m = {"RETEST_SUCCESS": (9.0, "broken level retested and held (role reversal)"),
         "RETEST": (4.0, "retest in progress"),
         "RETEST_FAILURE": (0.0, "retest failed — setup invalidated"),
         "BREAKOUT": (2.0, "broke out, retest not yet seen")}
    v = m.get(state, (0.0, "no retest context"))
    return round(v[0], 1), [v[1]]


def swing_score(*, swing_align: bool, swing_touch_count: int, swing_rejection_count: int) -> tuple[float, list[str]]:
    r = []
    s = 0.0
    if swing_align:
        s += 4.0
        r.append("structural swing aligns with direction (HL for CE / LH for PE)")
    s += min(3.0, (swing_touch_count or 0) * 1.0)
    s += min(3.0, (swing_rejection_count or 0) * 1.5)
    if swing_touch_count:
        r.append(f"zone tested {swing_touch_count}x, rejected {swing_rejection_count}x")
    return round(_clip(s, 0, 10), 1), r


def confluence_score(sub: dict, weights: dict | None = None) -> dict:
    """sub = {name: raw_subscore}. Returns the 0..100 weighted CONFLUENCE_SCORE
    (each sub normalised to its max, then weighted)."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    total = 0.0
    breakdown = {}
    for name, wt in w.items():
        raw = float(sub.get(name, 0.0))
        mx = SUBSCORE_MAX.get(name, 20)
        norm = raw / mx if mx else 0.0
        contrib = norm * wt * 100.0
        total += contrib
        breakdown[name] = {"raw": round(raw, 1), "out_of": mx,
                           "weight_pct": round(wt * 100, 1), "contribution": round(contrib, 1)}
    return {
        "confluence_score": round(_clip(total, 0, 100), 1),
        "breakdown": breakdown,
        "weights_source": "CONFIGURABLE_DEFAULT (NOT statistically calibrated — needs backtest)",
    }
