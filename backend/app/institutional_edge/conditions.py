"""
Named condition registry -- section 12's "Use available: ... Do not blindly
combine all features."

A small, fixed, single-feature-at-a-time set, each named plainly enough to
read as an economically interpretable hypothesis on its own (section 24:
"prefer simple, interpretable, stable, economically explainable
relationships"). No auto-generated combinations, no feature search -- if a
new condition is wanted, it gets added here explicitly, by name, one at a
time, same as this file's own existing entries.

Every field used below is a DIRECT column on scalp_signals already captured
at decision time (see app/db.py's schema) -- pcr, atr, momentum,
mtf_alignment, regime, tod_bucket, confidence, signal_type. No JSON parsing,
no derived feature invented here.
"""
from __future__ import annotations

from ..structural_break.regime_profiles import REGIME_NAMES


def _regime_is(name: str):
    return lambda row: row.get("regime") == name


CONDITIONS: dict[str, callable] = {
    **{f"regime=={r}": _regime_is(r) for r in REGIME_NAMES},
    "pcr>1.2": lambda row: (row.get("pcr") or 0) > 1.2,
    "pcr<0.8": lambda row: row.get("pcr") is not None and row["pcr"] < 0.8,
    "mtf_alignment>30": lambda row: (row.get("mtf_alignment") or 0) > 30,
    "mtf_alignment<-30": lambda row: (row.get("mtf_alignment") or 0) < -30,
    "confidence==HIGH": lambda row: row.get("confidence") == "HIGH",
    "tod_bucket==MORNING": lambda row: row.get("tod_bucket") == "MORNING",
    "tod_bucket==AFTERNOON": lambda row: row.get("tod_bucket") == "AFTERNOON",
    "signal_type==RESISTANCE_BREAKOUT": lambda row: row.get("signal_type") == "RESISTANCE_BREAKOUT",
    "signal_type==SUPPORT_BREAKDOWN": lambda row: row.get("signal_type") == "SUPPORT_BREAKDOWN",
    "signal_type==SUPPORT_REVERSAL": lambda row: row.get("signal_type") == "SUPPORT_REVERSAL",
    "signal_type==RESISTANCE_REVERSAL": lambda row: row.get("signal_type") == "RESISTANCE_REVERSAL",
}


def condition_fn(label: str):
    fn = CONDITIONS.get(label)
    if fn is None:
        raise KeyError(f"unknown condition {label!r} -- add it to CONDITIONS explicitly, "
                       "one named hypothesis at a time (see module docstring)")
    return fn


def condition_labels() -> list[str]:
    return list(CONDITIONS.keys())
