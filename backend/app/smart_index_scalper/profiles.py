"""
Paper-trading profiles (spec section 25).

CONSERVATIVE / BALANCED / AGGRESSIVE — each a config dict of thresholds. Values
are DEFAULTS and NOT calibrated (spec section 25/26). Full profile-driven paper
trading (state machine + journal) is a later slice; this module only holds the
config, and slice 3 (option selection) consumes `allowed_option_distance`.

Risk controls are NEVER bypassed — the paper-trade layer will still route
through autoscalp.safeguards.check_entry on top of these.
"""
from __future__ import annotations

import copy
import json
import os

PROFILES: dict[str, dict] = {
    "CONSERVATIVE": {
        "min_confidence": 80, "min_selection_score": 78, "min_rr1": 1.8,
        "max_trades_per_day": 3, "risk_per_trade_pct": 0.5, "max_daily_loss": 2000.0,
        "cooldown_sec": 900, "allowed_option_distance": 1,       # ATM or 1 strike OTM/ITM
        "sl_atr_mult": 1.0, "target_atr_mult": 2.2,
        "required_confirmations": ["level", "oi", "volume", "price_action"],
    },
    "BALANCED": {
        "min_confidence": 72, "min_selection_score": 68, "min_rr1": 1.4,
        "max_trades_per_day": 5, "risk_per_trade_pct": 0.75, "max_daily_loss": 3000.0,
        "cooldown_sec": 600, "allowed_option_distance": 2,
        "sl_atr_mult": 1.1, "target_atr_mult": 1.8,
        "required_confirmations": ["level", "oi", "price_action"],
    },
    "AGGRESSIVE": {
        "min_confidence": 65, "min_selection_score": 60, "min_rr1": 1.2,
        "max_trades_per_day": 8, "risk_per_trade_pct": 1.0, "max_daily_loss": 4000.0,
        "cooldown_sec": 300, "allowed_option_distance": 3,
        "sl_atr_mult": 1.3, "target_atr_mult": 1.6,
        "required_confirmations": ["level", "price_action"],
    },
}

DEFAULT_PROFILE = os.environ.get("SMART_SCALPER_PROFILE", "BALANCED").upper()


def get_profile(name: str | None = None, *, overrides: dict | None = None) -> dict:
    p = copy.deepcopy(PROFILES.get((name or DEFAULT_PROFILE).upper(), PROFILES["BALANCED"]))
    p["name"] = (name or DEFAULT_PROFILE).upper()
    env_ovr = os.environ.get("SMART_SCALPER_PROFILE_OVERRIDES")
    if env_ovr:
        try:
            p.update(json.loads(env_ovr))
        except Exception:
            pass
    if overrides:
        p.update(overrides)
    p["calibration"] = "UNCALIBRATED — profile thresholds are defaults (spec section 25/26)"
    return p


def list_profiles() -> dict:
    return {k: {**v, "name": k} for k, v in PROFILES.items()}
