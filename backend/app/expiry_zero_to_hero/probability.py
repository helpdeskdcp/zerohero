"""
ZeroToHeroProbabilityEngine — an interpretable score, deliberately conservative.

STATE: UNCALIBRATED. There is exactly ONE token-resolvable SENSEX expiry of
historical option data (03-Sep-2026) — see EXPIRY_ZERO_TO_HERO.md. With N=1
no coefficient can be fit without overfitting, so:
  * the weights below are PRIORS from option theory, not fitted values;
  * the engine returns calibration_status = "UNCALIBRATED";
  * the signal layer forces NO_TRADE / WATCH until >= MIN_EXPIRIES clean days
    are collected forward and a walk-forward fit exists.

Factors (all causal, from features.py + support_detector + the OI engine which
is LIVE-ONLY):
  premium_support_strength   0..100   (repeated-test pattern)
  n_support_tests            int
  premium_compression        smaller = more coiled  -> inverted
  spot_momentum_dir          aligned with the option side?  (PE wants spot down)
  gamma_accel_potential      0.5*gamma*spot_speed^2   (the physical "why")
  mins_to_expiry             closer = bigger gamma, less time to be wrong
  atm_distance_norm          |K-S| / (recent spot range)   -> near strike better
  oi_imbalance               LIVE ONLY -> None in historical replay
"""
from __future__ import annotations

MIN_EXPIRIES_FOR_CALIBRATION = 8

# theory priors (NOT fitted). Documented in the research doc.
_W = {
    "support_strength": 0.020,      # per strength point
    "n_tests_bonus": 0.15,          # per test beyond the first
    "compression": 0.6,             # * (1 - compression), coiled premium
    "spot_align": 0.9,              # side-aligned spot momentum present
    "gamma_potential": 0.004,       # per rupee of 0.5*gamma*v^2
    "expiry_proximity": 0.012,      # per minute under 45 to expiry
    "near_strike": 0.5,             # * (1 - atm_distance_norm)
    "oi_imbalance": 0.010,          # per % of dominant-side OI  (LIVE ONLY)
    "bias": -2.4,                   # low base rate — these are rare
}


def _sigmoid(x):
    import math
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


class ZeroToHeroProbabilityEngine:
    def score(self, *, side: str, feats: dict, support: dict,
              oi_imbalance_pct: float | None = None,
              recent_spot_range: float | None = None) -> dict:
        f = feats or {}
        sup = support or {}
        contribs = {}

        contribs["support_strength"] = _W["support_strength"] * (sup.get("strength") or 0)
        contribs["n_tests_bonus"] = _W["n_tests_bonus"] * max(0, (sup.get("number_of_tests") or 0) - 1)

        comp = f.get("prem_compression")
        contribs["compression"] = _W["compression"] * (1.0 - min(1.0, comp)) if comp is not None else 0.0

        sm = f.get("spot_momentum")
        aligned = (side == "PE" and (sm or 0) < 0) or (side == "CE" and (sm or 0) > 0)
        contribs["spot_align"] = _W["spot_align"] if aligned and abs(sm or 0) > 0.05 else 0.0

        gp = f.get("gamma_accel_potential")
        contribs["gamma_potential"] = _W["gamma_potential"] * min(400.0, gp) if gp else 0.0

        mte = f.get("mins_to_expiry")
        contribs["expiry_proximity"] = _W["expiry_proximity"] * max(0.0, 45.0 - mte) if mte is not None else 0.0

        adn = None
        if f.get("atm_distance_pts") is not None and recent_spot_range:
            adn = min(1.0, f["atm_distance_pts"] / max(1.0, recent_spot_range))
            contribs["near_strike"] = _W["near_strike"] * (1.0 - adn)
        else:
            contribs["near_strike"] = 0.0

        if oi_imbalance_pct is not None:
            contribs["oi_imbalance"] = _W["oi_imbalance"] * oi_imbalance_pct
            oi_state = "USED_LIVE"
        else:
            contribs["oi_imbalance"] = 0.0
            oi_state = "UNAVAILABLE (historical) — factor omitted"

        z = _W["bias"] + sum(contribs.values())
        p = _sigmoid(z)
        return {
            "raw_z": round(z, 3),
            "probability_pct": round(p * 100, 1),
            "contributions": {k: round(v, 4) for k, v in contribs.items()},
            "oi_factor": oi_state,
            "calibration_status": "UNCALIBRATED",
            "calibration_sample_expiries": 1,
            "min_expiries_needed": MIN_EXPIRIES_FOR_CALIBRATION,
            "weights_source": "OPTION_THEORY_PRIOR (not fitted)",
            "atm_distance_norm": round(adn, 3) if adn is not None else None,
        }
