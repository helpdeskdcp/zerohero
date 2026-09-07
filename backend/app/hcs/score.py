"""
HCS quality score -- a transparent 0-100 CONFLUENCE score.

NOT a probability. The win-probability stays the existing calibrated
`decide_from_context` `probability`. This score answers a different question:
"how much independent, mutually-agreeing evidence backs this setup?"

Each contributing module returns an agreement in [-1, +1] (against / neutral /
for the setup direction). The score is a fixed-weight blend, rescaled to 0-100,
then penalised for missing (UNOBSERVABLE) modules so a thin evidence base can
never score A+.
"""
from __future__ import annotations

# fixed seed weights (sum ~1.0 over the observable set). Deliberately spread so
# no single module dominates. Calibrating these needs a multi-regime outcome
# history that does not exist yet (see HCS_CALIBRATION_REPORT.md).
_W = {
    "abnormal_spike": 0.10, "spike_reaction": 0.08, "breakout_retest": 0.08,
    "vwap_ema": 0.12, "momentum": 0.12, "relative_volume": 0.08,
    "oi_structure": 0.12, "support_resistance": 0.10, "regime": 0.06,
    "mtf_alignment": 0.14, "exhaustion": 0.05, "fake_breakout": 0.05,
    "setup_memory": 0.10,
}


def _agree(mod: dict, direction: str) -> float | None:
    """Map one module's value onto [-1, +1] agreement with `direction`."""
    v = mod.get("value")
    st = mod.get("status")
    if st in ("UNOBSERVABLE", "NA") or v is None:
        return None
    if isinstance(v, dict):
        for k in ("agrees_direction", "ltp_vs_vwap_agrees"):
            if k in v and v[k] is not None:
                return 1.0 if v[k] else -1.0
        if "mtf_alignment" in v and v["mtf_alignment"] is not None:
            a = max(-1.0, min(1.0, float(v["mtf_alignment"])))
            return a if direction == "BULLISH" else -a if direction == "BEARISH" else 0.0
        if "oi_bias" in v:
            return 1.0 if v["oi_bias"] == direction else -1.0 if v["oi_bias"] not in ("FLAT", direction) else 0.2
        if "regime" in v:
            return {"TRENDING_UP": 0.6, "TRENDING_DOWN": 0.6, "TRENDING": 0.6,
                    "STRONG_TREND": 0.8, "RANGE": 0.0, "UNSTABLE": -0.6}.get(v["regime"], 0.0)
        if "dist_support_atr" in v:  # S/R: room in the trade direction is good
            room = v.get("dist_resistance_atr") if direction == "BULLISH" else v.get("dist_support_atr")
            if room is None:
                return 0.0
            return max(-1.0, min(1.0, (room - 0.5)))     # >0.5 ATR of room -> positive
    return 0.0


def compute(evidence: dict, memory: dict, *, direction: str) -> dict:
    mods = evidence["modules"]
    # inject setup-memory as an agreement in [-1,+1] from its shrunk win-rate
    mem_agree = None
    if memory.get("status") == "OK":
        mem_agree = max(-1.0, min(1.0, (memory["shrunk_win_rate"] - 0.5) * 4))
    contribs = []
    num = den = 0.0
    observed = 0
    total = 0
    for key, w in _W.items():
        total += 1
        if key == "setup_memory":
            a = mem_agree
        else:
            m = mods.get(key) or {}
            a = _agree(m, direction) if m.get("contributes", True) else None
        if a is None:
            contribs.append({"module": key, "weight": w, "agreement": None,
                             "contribution": 0.0, "status": (mods.get(key) or {}).get("status", "NA")})
            continue
        observed += 1
        c = w * a
        num += c
        den += w
        contribs.append({"module": key, "weight": w, "agreement": round(a, 3),
                         "contribution": round(c, 4),
                         "status": (mods.get(key) or {}).get("status", "OK")})
    raw = (num / den) if den > 0 else 0.0          # [-1, +1]
    coverage = observed / total
    # 0-100, then multiply by evidence coverage (thin base -> capped)
    score = max(0.0, min(100.0, (raw + 1.0) * 50.0)) * (0.5 + 0.5 * coverage)
    return {
        "hcs_score": round(score, 1),
        "raw_agreement": round(raw, 3),
        "evidence_coverage": round(coverage, 3),
        "observed_modules": observed, "total_modules": total,
        "components": sorted(contribs, key=lambda x: -abs(x["contribution"])),
    }
