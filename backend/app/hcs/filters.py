"""
HCS hard-filter veto layer.

Each filter returns a veto dict or None. Any veto forces the final decision to
NO_TRADE regardless of the HCS quality score (the ask: "Hard filters must be able
to override a high score"). Every veto carries an exact reason.
"""
from __future__ import annotations

DEFAULTS = {
    "max_opt_spread_pct": 1.2,       # matches autoscalp safeguards.max_spread_pct
    "min_rr": 1.3,
    "min_data_quality": 0.35,
    "sr_block_atr": 0.35,            # opposing S/R closer than this many ATR -> veto
    "min_ev_r": 0.05,
}


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def run(snap: dict, ev: dict, cfg: dict | None = None) -> list:
    c = {**DEFAULTS, **(cfg or {})}
    M = ev["modules"]
    direction = ev["direction"]
    vetoes = []

    def veto(name, reason, sev="HARD"):
        vetoes.append({"filter": name, "reason": reason, "severity": sev})

    # 1. bad liquidity / spread
    spr = _f(ev.get("atm_option_spread_pct"))
    if spr is not None and spr > c["max_opt_spread_pct"]:
        veto("liquidity_spread", f"ATM option spread {spr:.2f}% > {c['max_opt_spread_pct']}%")
    dq = _f(snap.get("data_quality_score"))
    if dq is not None and dq < c["min_data_quality"]:
        veto("data_quality", f"data_quality_score {dq:.2f} < {c['min_data_quality']}")
    if snap.get("data_quality") == "invalid_volume":
        veto("data_quality", "feed flagged invalid_volume for this symbol/cycle")

    # 2. contradictory MTF
    mtf = _f(snap.get("mtf_alignment"))
    if mtf is not None and direction != "NONE":
        if direction == "BULLISH" and mtf <= -0.25:
            veto("mtf_conflict", f"MTF alignment {mtf:+.2f} opposes a bullish setup")
        if direction == "BEARISH" and mtf >= 0.25:
            veto("mtf_conflict", f"MTF alignment {mtf:+.2f} opposes a bearish setup")

    # 3. exhaustion  (regime UNSTABLE + a directional setup is a proxy here)
    if snap.get("regime") == "UNSTABLE" and direction != "NONE":
        veto("exhaustion", "regime UNSTABLE under a directional setup -> treat as exhausted/noisy")

    # 4. fake breakout
    nrc = str(snap.get("no_trade_reason_class") or "")
    if "false" in nrc.lower() or "fake" in nrc.lower():
        veto("fake_breakout", f"upstream no_trade_reason_class = {nrc}")
    fr = M.get("fake_breakout", {}).get("value", {})
    # (LIKELY_FALSE already forces NONE upstream; this catches the advisory case)

    # 5. poor RR / EV
    rr = _f(snap.get("rr"))
    if rr is not None and rr < c["min_rr"]:
        veto("poor_rr", f"RR {rr:.2f} < {c['min_rr']}")
    evr = _f(snap.get("ev_r"))
    if evr is not None and evr < c["min_ev_r"]:
        veto("poor_ev", f"EV/R {evr:.2f} < {c['min_ev_r']}")

    # 6. major nearby opposing S/R
    sr = M.get("support_resistance", {}).get("value", {})
    if direction == "BULLISH":
        d = _f(sr.get("dist_resistance_atr"))
        strong = (_f(sr.get("resistance_strength")) or 0) >= 0.5
        if d is not None and d < c["sr_block_atr"] and strong:
            veto("nearby_sr", f"strong resistance only {d:.2f} ATR above a bullish entry")
    elif direction == "BEARISH":
        d = _f(sr.get("dist_support_atr"))
        strong = (_f(sr.get("support_strength")) or 0) >= 0.5
        if d is not None and d < c["sr_block_atr"] and strong:
            veto("nearby_sr", f"strong support only {d:.2f} ATR below a bearish entry")

    # 7. abnormal / noisy conditions
    cs = str(snap.get("calibration_status") or "")
    if cs in ("invalid", "prior") and direction != "NONE":
        veto("noisy_calibration", f"calibration_status={cs} -> probability is a bare prior, not fitted",
             sev="SOFT")

    return vetoes
