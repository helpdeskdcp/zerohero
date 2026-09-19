"""
Phase F -- deterministic effective-profile selection.

    symbol -> InstrumentProfile -> regime -> RegimeProfile -> EffectiveProfile

Pure function: takes only (symbol, regime, is_expiry_day) -- all three
already known to the caller at decision time in the existing runner.py
flow -- and returns a fully resolved, auditable configuration. No DB
reads, no network calls, no time-dependence beyond what the caller
already resolved. This is the no-look-ahead guarantee: profile selection
cannot see anything the live decision loop didn't already have in hand.
"""
from __future__ import annotations

from dataclasses import dataclass

from .instrument_profiles import get_instrument_profile, _BASE
from .regime_profiles import get_regime_profile


@dataclass
class EffectiveProfile:
    symbol: str
    regime: str
    is_expiry_day: bool
    resolved: dict            # name -> final numeric/str value actually in effect
    provenance: dict          # name -> {"value", "status", "source"} -- which layer won
    cost_model_status: str
    instrument_validation_status: str

    def audit_line(self) -> str:
        """One-line, log-friendly summary -- matches the PROFILE=...
        REGIME=... COST=... format requested for decision-path logging."""
        return (f"PROFILE={self.symbol} REGIME={self.regime} "
                f"EXPIRY_DAY={self.is_expiry_day} COST={self.cost_model_status} "
                f"INSTRUMENT_STATUS={self.instrument_validation_status}")

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "regime": self.regime,
                "is_expiry_day": self.is_expiry_day, "resolved": self.resolved,
                "provenance": self.provenance, "cost_model_status": self.cost_model_status,
                "instrument_validation_status": self.instrument_validation_status,
                "audit_line": self.audit_line()}


def select_effective_profile(symbol: str, regime: str = "NORMAL_DAY",
                             is_expiry_day: bool = False) -> EffectiveProfile:
    """Deterministic merge order: _BASE <- instrument profile <- regime
    override (regime override only applies when is_expiry_day is True for
    the EXPIRY_DAY regime, matching runner.py's existing is_expiry_day gate
    exactly -- a non-expiry-day call never picks up expiry_day_profile,
    same as today's live behavior)."""
    inst = get_instrument_profile(symbol)
    regime_key = "EXPIRY_DAY" if is_expiry_day else str(regime or "NORMAL_DAY").upper()
    reg = get_regime_profile(regime_key)

    resolved: dict = {}
    provenance: dict = {}
    for name, base_param in _BASE.items():
        chosen = base_param
        layer = "BASE"
        inst_param = inst.params.get(name)
        if inst_param is not None:
            chosen, layer = inst_param, "INSTRUMENT"
        reg_param = reg.overrides.get(name)
        if reg_param is not None:
            chosen, layer = reg_param, "REGIME"
        resolved[name] = chosen.value
        provenance[name] = {"value": chosen.value, "status": chosen.status.value,
                            "source": chosen.source, "layer": layer}

    return EffectiveProfile(
        symbol=inst.symbol, regime=regime_key, is_expiry_day=bool(is_expiry_day),
        resolved=resolved, provenance=provenance,
        cost_model_status=inst.cost_model_status,
        instrument_validation_status=inst.validation_status.value,
    )
