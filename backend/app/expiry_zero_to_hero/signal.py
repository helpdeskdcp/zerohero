"""
ZeroToHeroSignalEngine + ExpiryZeroToHeroReporter.

The engine NEVER claims certainty and, while UNCALIBRATED, cannot emit a live
"ENTRY" status — the strongest it will say is WATCH. Expected-premium bands are
MODEL projections (bs.decompose_move), explicitly labelled.
"""
from __future__ import annotations

from . import bs

STATUSES = ("NO_TRADE", "WATCH", "ENTRY_CANDIDATE", "CONFIRMED", "INVALIDATED")


class ZeroToHeroSignalEngine:
    def __init__(self, *, allow_live_status=False):
        # gate: even a strong pattern stays WATCH until calibrated + operator opt-in
        self.allow_live_status = allow_live_status

    def evaluate(self, *, index, expiry, minute, side, strike, feats, support,
                 prob: dict, spot_now, mins_to_expiry,
                 expected_spot_move_pts: float, iv_now=None) -> dict:
        p = prob.get("probability_pct", 0.0)
        strong = (support.get("verdict") in ("STRONG", "MODERATE")
                  and prob.get("contributions", {}).get("spot_align", 0) > 0
                  and (mins_to_expiry or 99) <= 45)

        if prob.get("calibration_status") != "CALIBRATED" or not self.allow_live_status:
            status = "WATCH" if strong else "NO_TRADE"
        else:                                          # future: calibrated path
            status = "ENTRY_CANDIDATE" if (strong and p >= 55) else ("WATCH" if p >= 35 else "NO_TRADE")

        # MODEL expected-premium bands from a directional spot move of the given size
        entry_lo = round((feats.get("prem_range_10m") or 0) and 0, 2)
        prem_now = feats.get("_prem_now")
        bands = None
        if prem_now and spot_now:
            is_call = side == "CE"
            sgn = -1 if side == "PE" else 1              # PE profits when spot falls
            projections = {}
            for name, frac in (("conservative", 0.5), ("base", 1.0), ("aggressive", 1.6)):
                s1 = spot_now + sgn * expected_spot_move_pts * frac
                d = bs.decompose_move(S0=spot_now, S1=s1, K=strike, is_call=is_call,
                                      mins0=mins_to_expiry, mins1=max(0.5, (mins_to_expiry or 1) - 8),
                                      prem0=prem_now, prem1=prem_now, sigma0=iv_now, d_iv=0.0)
                proj = prem_now + d["delta_term"] + d["gamma_term"] + d["theta_term"]
                # near expiry, floor at intrinsic of the projected spot
                intr = max(0.0, (strike - s1) if not is_call else (s1 - strike))
                projections[name] = round(max(proj, intr), 1)
            bands = projections

        # adverse: if the move does NOT come, premium bleeds to ~time-value decay + small drift
        adverse = None
        if prem_now:
            adverse = round(min(prem_now * 0.35, prem_now - (feats.get("prem_range_10m") or prem_now * 0.3)), 1)

        return {
            "engine": "EXPIRY ZERO TO HERO",
            "index": index, "expiry": expiry, "time": minute,
            "side": side, "strike": strike,
            "entry_zone": [round((prem_now or 0) * 0.95, 1), round((prem_now or 0) * 1.05, 1)] if prem_now else None,
            "zero_to_hero_probability_pct": p,
            "premium_support": support.get("verdict"),
            "support_tests": support.get("number_of_tests"),
            "oi_imbalance_pct": prob.get("_oi_imbalance_pct"),
            "spot_direction": ("BEARISH" if side == "PE" else "BULLISH")
            if prob.get("contributions", {}).get("spot_align", 0) > 0 else "UNCLEAR",
            "gamma_acceleration": ("HIGH" if (feats.get("gamma_accel_potential") or 0) > 40
                                   else "MEDIUM" if (feats.get("gamma_accel_potential") or 0) > 10 else "LOW"),
            "time_remaining_min": mins_to_expiry,
            "expected_premium_MODEL": bands,
            "risk_max_adverse_premium_MODEL": adverse,
            "status": status,
            "calibration_status": prob.get("calibration_status"),
            "disclaimer": "UNCALIBRATED research engine — probability is a theory-prior score, "
                          "not an empirically validated frequency. NOT for live trading.",
        }


class ExpiryZeroToHeroReporter:
    def render(self, sig: dict) -> str:
        L = [
            "EXPIRY ZERO TO HERO",
            "-------------------",
            f"INDEX: {sig.get('index')}",
            f"EXPIRY: {sig.get('expiry')}",
            f"TIME: {sig.get('time')}",
            "",
            f"SIDE: {sig.get('side')}",
            f"STRIKE: {sig.get('strike')}",
            f"ENTRY ZONE: {sig.get('entry_zone')}",
            "",
            f"ZERO-TO-HERO PROBABILITY: {sig.get('zero_to_hero_probability_pct')}%  "
            f"[{sig.get('calibration_status')}]",
            "",
            f"PREMIUM SUPPORT: {sig.get('premium_support')}",
            f"SUPPORT TESTS: {sig.get('support_tests')}",
            f"OI IMBALANCE: {sig.get('oi_imbalance_pct')}",
            f"SPOT DIRECTION: {sig.get('spot_direction')}",
            f"GAMMA ACCELERATION: {sig.get('gamma_acceleration')}",
            f"TIME REMAINING: {sig.get('time_remaining_min')} min",
            "",
            "EXPECTED PREMIUM (MODEL:BS projection):",
            f"  Conservative: {(sig.get('expected_premium_MODEL') or {}).get('conservative')}",
            f"  Base:         {(sig.get('expected_premium_MODEL') or {}).get('base')}",
            f"  Aggressive:   {(sig.get('expected_premium_MODEL') or {}).get('aggressive')}",
            "",
            f"RISK (MODEL): max expected adverse premium ~ {sig.get('risk_max_adverse_premium_MODEL')}",
            "",
            f"STATUS: {sig.get('status')}",
            "",
            sig.get("disclaimer", ""),
        ]
        return "\n".join(L)
