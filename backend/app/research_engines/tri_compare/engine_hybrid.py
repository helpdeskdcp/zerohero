"""
ENGINE 3 -- HYBRID.

Engine 1 (Trend) supplies the directional REGIME gate; Engine 2 (Structure)
supplies LOCATION + entry TIMING. Combined ONLY through two INDEPENDENT,
explainable gates -- never a blind score sum:

  gate A (trend, from Engine 1 primitives):
      'align'       -> the E1 trend state must match the structure direction
      'not_counter' -> the E1 trend state must not oppose it
      + ADX >= trend_gate_min_adx, + (optional) efficiency ratio >= min
  gate B (structure + timing, the full Engine 2 pipeline):
      structure signal -> location -> momentum -> confirmation -> two-stage entry

A trade fires only if gate A AND gate B pass. Thresholds are configurable.
"""
from __future__ import annotations

from . import engine_structure as E2
from .engine_trend import _trend_state


def _trend_gate_ok(r: dict, direction: str, e3: dict) -> bool:
    st = _trend_state(r)
    if e3["trend_gate"] == "align":
        if not ((direction == "LONG" and st == 1) or (direction == "SHORT" and st == -1)):
            return False
    else:  # not_counter
        if (direction == "LONG" and st == -1) or (direction == "SHORT" and st == 1):
            return False
    if (r.get("adx") or 0.0) < e3["trend_gate_min_adx"]:
        return False
    if e3["trend_gate_require_eff_ratio"] and (r.get("eff_ratio") or 0.0) < e3["trend_gate_eff_ratio_min"]:
        return False
    return True


def signals(frame: list[dict], bars: list[dict], cfg: dict) -> list[dict]:
    e3 = cfg["e3"]
    # gate B = the FULL Engine-2 pipeline, but with the hybrid entry-quality bar.
    # Reuse E2.signals via a synthetic engine key so its eq_min is e3.e2_eq_min.
    e2_over = dict(cfg["e2"])
    e2_over["eq_min"] = e3["e2_eq_min"]
    cfg_h = dict(cfg)
    cfg_h["e2_hybrid"] = e2_over
    b_signals = E2.signals(frame, bars, cfg_h, e_key="e2_hybrid")

    out: list[dict] = []
    for s in b_signals:
        i = s["signal_index"]
        r = frame[i]
        gate_a = _trend_gate_ok(r, s["direction"], e3)
        if s.get("status") != "ENTRY_READY" or s.get("fill_index") is None:
            out.append({**s, "engine": "E3_HYBRID", "gate_a_trend": gate_a})
            continue
        if not gate_a:
            out.append({"engine": "E3_HYBRID", "signal_index": i, "direction": s["direction"],
                        "kind": "hybrid", "status": "REJECTED_TREND_GATE",
                        "fill_index": None, "gate_a_trend": False})
            continue
        out.append({**s, "engine": "E3_HYBRID", "gate_a_trend": True, "kind": "hybrid"})
    return out
