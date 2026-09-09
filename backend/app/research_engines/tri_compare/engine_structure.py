"""
ENGINE 2 -- PRECISION STRUCTURE ENGINE  (the ChatGPT-spec engine).

    STRUCTURE  ->  LOCATION  ->  MOMENTUM  ->  CONFIRMATION  ->  MATHEMATICAL ENTRY

STRUCTURE     : BOS / CHoCH from HH-HL / LH-LL (from orderflow.build), or a
                validated pierce+reclaim.
LOCATION      : the signal bar sits within `location_atr` ATR of a validated S/R
                level (recent pivot / value-area edge).
MOMENTUM      : RSI on the right side of 50, ADX >= min, +DI/-DI aligned.
CONFIRMATION  : the signal bar is a real body (candle strength).
ENTRY         : NOT the signal. Two-stage SIGNAL_DETECTED -> WAITING_FOR_RETEST
                -> ENTRY_READY via orderflow.entry_timing (pullback into the
                calibrated zone + momentum-recovery bar + no-chase filter). The
                harness then applies the SHARED RR gate + ATR-SL + exit.
"""
from __future__ import annotations

from . import _entry


_LONG_STATES = {"BOS_UP", "CHOCH_UP", "HH_HL"}
_SHORT_STATES = {"BOS_DN", "CHOCH_DN", "LH_LL"}


def _structure_signal(r: dict, e: dict) -> str | None:
    st = r.get("structure_state")
    if st in e["signal_states"]:
        return "LONG" if st.endswith("_UP") else "SHORT"
    if e["allow_reclaim_signal"]:
        rc = r.get("reclaim") or {}
        if rc.get("fired"):
            dr = rc.get("dist_ratio") or 0.0
            if 0.20 < dr <= 0.65:              # Stage-7 sweet band
                return "LONG" if rc["dir"] > 0 else "SHORT"
    return None


def signals(frame: list[dict], bars: list[dict], cfg: dict, *,
            e_key: str = "e2") -> list[dict]:
    e = cfg[e_key]
    warm = cfg["warmup_bars"]
    eq_min = e.get("eq_min", e.get("e2_eq_min", 45.0))
    out: list[dict] = []
    prev_dir = None
    last_fire_i = -10_000
    for i in range(warm, len(frame)):
        r = frame[i]
        direction = _structure_signal(r, e)
        if direction is None:
            continue
        if cfg.get("side_filter") and direction != cfg["side_filter"]:
            continue

        # LOCATION
        d = r.get("sr_dist_atr")
        if d is None or d > e["location_atr"]:
            continue
        # MOMENTUM
        rsi = r.get("rsi")
        if rsi is None:
            continue
        if direction == "LONG" and rsi < e["rsi_long_min"]:
            continue
        if direction == "SHORT" and rsi > e["rsi_short_max"]:
            continue
        if (r.get("adx") or 0.0) < e["adx_min"]:
            continue
        if e["require_di_align"]:
            dp, dm = r.get("di_plus"), r.get("di_minus")
            if dp is None or dm is None:
                continue
            if direction == "LONG" and not dp > dm:
                continue
            if direction == "SHORT" and not dm > dp:
                continue
        # CONFIRMATION (candle strength on the signal bar)
        if (r.get("body_frac") or 0.0) < e["candle_body_min_frac"]:
            continue

        if direction == prev_dir and (i - last_fire_i) < cfg["cooldown_bars"]:
            continue

        rk = round(min(100.0, 45.0 + (r.get("adx") or 0) * 0.6 + abs((rsi - 50.0)) * 0.4), 1)
        stage = _entry.two_stage(bars, frame, i, direction, rk, e, cfg)
        prev_dir, last_fire_i = direction, i
        if stage.get("status") != "ENTRY_READY":
            out.append({"engine": e_key.upper(), "signal_index": i, "direction": direction,
                        "kind": "structure", "status": stage.get("status"),
                        "reason": stage.get("reason"), "fill_index": None})
            continue
        if stage.get("entry_quality", 0) < eq_min:
            out.append({"engine": e_key.upper(), "signal_index": i, "direction": direction,
                        "kind": "structure", "status": "REJECTED_LOW_EQ",
                        "entry_quality": stage.get("entry_quality"), "fill_index": None})
            continue
        out.append({
            "engine": e_key.upper(), "signal_index": i, "fill_index": stage["fill_index"],
            "direction": direction, "entry_ref": stage["entry"],
            "rk_score": rk, "entry_quality": stage["entry_quality"],
            "bars_waited": stage["bars_waited"], "kind": "structure",
            "status": "ENTRY_READY",
            "context": {"structure_state": r.get("structure_state"),
                        "sr_dist_atr": round(d, 2), "rsi": round(rsi, 1),
                        "adx": round(r.get("adx") or 0, 1),
                        "regime": f'{r.get("regime_trend")}/{r.get("regime_vol")}'},
        })
    return out
