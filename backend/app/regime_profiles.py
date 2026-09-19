"""
Phase F -- Regime Profile overlay. Independent of InstrumentProfile
(app.instrument_profiles) by design: a regime profile is a set of
parameter *overrides* that could apply on top of ANY instrument's base
profile, never a full duplicate strategy per (symbol, regime) pair.

Regime detection itself is unchanged and lives in
app.autoscalp.regime_mtf::detect_regime() -- this module only defines
what to DO once a regime is known, and does so honestly: every regime
below except EXPIRY_DAY has an empty override set (status DEFAULT),
because no regime-conditional threshold has ever been validated against
real trade outcomes in this codebase. Inventing per-regime numbers to
"look complete" would be exactly the overfitting risk Phase F exists to
prevent -- an empty, explicitly-labeled override is the honest answer
until real evidence exists.
"""
from __future__ import annotations

from dataclasses import dataclass

from .instrument_profiles import Param, ParamStatus


REGIMES = ("TRENDING_UP", "TRENDING_DOWN", "RANGE", "HIGH_VOLATILITY",
          "LOW_VOLATILITY", "EXPIRY_DAY", "NORMAL_DAY")


@dataclass
class RegimeProfile:
    regime: str
    overrides: dict          # name -> Param; empty = no-op (falls through to instrument profile)
    status: ParamStatus
    rationale: str

    def to_dict(self) -> dict:
        return {"regime": self.regime,
                "overrides": {k: v.to_dict() for k, v in self.overrides.items()},
                "status": self.status.value, "rationale": self.rationale}


# app.autoscalp.runner.DEFAULT_CONFIG's existing expiry_day_profile, restated
# as governed Params -- same values, same source, not new numbers. This is
# the ONE regime override that has real, documented, already-live rationale.
_EXPIRY_DAY_OVERRIDES = {
    "max_hold_sec": Param(480, ParamStatus.DERIVED,
                          "app/autoscalp/runner.py DEFAULT_CONFIG.expiry_day_profile",
                          "0-DTE theta decay is severe; a scalp that hasn't paid in a "
                          "few minutes is decaying, not developing -- far shorter hold "
                          "than the ~1500s base."),
    "t1_atr": Param(1.1, ParamStatus.DERIVED,
                    "app/autoscalp/runner.py DEFAULT_CONFIG.expiry_day_profile",
                    "Faster target on 0-DTE gamma moves."),
    "t2_atr": Param(1.8, ParamStatus.DERIVED,
                    "app/autoscalp/runner.py DEFAULT_CONFIG.expiry_day_profile", ""),
    "sl_atr": Param(0.9, ParamStatus.DERIVED,
                    "app/autoscalp/runner.py DEFAULT_CONFIG.expiry_day_profile", ""),
    "rr_min": Param(1.15, ParamStatus.DERIVED,
                    "app/autoscalp/runner.py DEFAULT_CONFIG.expiry_day_profile",
                    "Slightly relaxed RR floor to compensate for tighter 0-DTE geometry."),
    "est_cost_r": Param(0.12, ParamStatus.DERIVED,
                        "app/autoscalp/runner.py DEFAULT_CONFIG.expiry_day_profile",
                        "0-DTE spreads widen into the close; higher pre-trade cost haircut."),
}

REGISTRY: dict[str, RegimeProfile] = {
    "EXPIRY_DAY": RegimeProfile(
        "EXPIRY_DAY", _EXPIRY_DAY_OVERRIDES, ParamStatus.DERIVED,
        "Real, already-live overrides for an NSE/BSE index's own expiry day "
        "(app.autoscalp.runner's is_expiry_day gate, NFO/BFO only, never MCX)."),
    "NORMAL_DAY": RegimeProfile("NORMAL_DAY", {}, ParamStatus.DEFAULT,
                                "No override -- falls through to the instrument profile."),
    "TRENDING_UP": RegimeProfile("TRENDING_UP", {}, ParamStatus.DEFAULT,
                                 "No regime-conditional threshold has been validated for "
                                 "this regime; ZEROHERO_TRADING_EDGE_VALIDATION_2026-09-19.md "
                                 "found TRENDING_UP is the WORST-performing regime in the "
                                 "post-fix sample (n=12, PF 0.367) but attributed this to a "
                                 "structural entry-timing issue (continuation setups blocked "
                                 "by default), not a threshold that a regime override could fix -- "
                                 "inventing one here would be tuning on noise."),
    "TRENDING_DOWN": RegimeProfile("TRENDING_DOWN", {}, ParamStatus.DEFAULT,
                                   "No regime-conditional threshold has been validated."),
    "RANGE": RegimeProfile("RANGE", {}, ParamStatus.DEFAULT,
                           "No regime-conditional threshold has been validated."),
    "HIGH_VOLATILITY": RegimeProfile("HIGH_VOLATILITY", {}, ParamStatus.DEFAULT,
                                     "No regime-conditional threshold has been validated; "
                                     "insufficient real sample exists to distinguish this "
                                     "regime's trades from the pooled result."),
    "LOW_VOLATILITY": RegimeProfile("LOW_VOLATILITY", {}, ParamStatus.DEFAULT,
                                    "No regime-conditional threshold has been validated."),
}


def get_regime_profile(regime: str) -> RegimeProfile:
    """Deterministic, pure lookup. Unknown/unrecognized regime names get the
    same no-op NORMAL_DAY treatment -- never a guessed override."""
    key = str(regime or "").upper()
    return REGISTRY.get(key, REGISTRY["NORMAL_DAY"])
