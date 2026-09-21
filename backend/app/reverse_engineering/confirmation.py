"""
Phase 7 -- multi-group confirmation engine. SHADOW/RESEARCH ONLY: nothing
here is wired into app.autoscalp.runner or any live/paper trade path, and
nothing here places or recommends a broker order.

The 9 evidence groups from the spec:
  A price_action, B volume, C oi, D option_premium, E vwap_trend,
  F support_resistance, G orderflow_depth, H volatility, I regime

Each group must independently report one of:
  EVIDENCE_FOR | EVIDENCE_AGAINST | NO_EVIDENCE | DATA_UNAVAILABLE

DESIGN CHOICE (not a weighted sum, by the spec's own explicit instruction):
CONFIRMED requires corroboration from >=3 independent groups with
EVIDENCE_FOR, ZERO groups with EVIDENCE_AGAINST, and fewer than half the
groups DATA_UNAVAILABLE (a data-quality floor -- a "confirmed" call built
mostly on missing data is not a confirmation, it's a guess). Any
EVIDENCE_AGAINST alongside at least one EVIDENCE_FOR is a genuine conflict
-> INVALIDATED, not averaged away. A single EVIDENCE_FOR group, however
strong, can only ever reach SETUP_FORMING -- this is the corroboration
requirement, tested explicitly.
"""
from __future__ import annotations

GROUPS = ("price_action", "volume", "oi", "option_premium", "vwap_trend",
         "support_resistance", "orderflow_depth", "volatility", "regime")

_VALID_VALUES = {"EVIDENCE_FOR", "EVIDENCE_AGAINST", "NO_EVIDENCE", "DATA_UNAVAILABLE"}

_MIN_FOR_TO_CONFIRM = 3


def evaluate_confirmation(groups: dict) -> dict:
    """`groups` maps a subset (or all) of GROUPS to one of _VALID_VALUES.
    A missing key is treated the same as DATA_UNAVAILABLE (never silently
    NO_EVIDENCE -- "we didn't check" and "we checked and found nothing"
    are different things and must not be conflated)."""
    resolved = {}
    for g in GROUPS:
        v = groups.get(g, "DATA_UNAVAILABLE")
        if v not in _VALID_VALUES:
            v = "DATA_UNAVAILABLE"
        resolved[g] = v

    evidence_for = [g for g, v in resolved.items() if v == "EVIDENCE_FOR"]
    evidence_against = [g for g, v in resolved.items() if v == "EVIDENCE_AGAINST"]
    missing = [g for g, v in resolved.items() if v == "DATA_UNAVAILABLE"]
    no_evidence = [g for g, v in resolved.items() if v == "NO_EVIDENCE"]

    n = len(GROUPS)
    data_quality_ok = len(missing) < (n / 2.0)

    if evidence_for and evidence_against:
        state = "INVALIDATED"
    elif not evidence_for and not evidence_against:
        state = "NO_TRADE"
    elif evidence_against and not evidence_for:
        state = "NO_TRADE"
    elif len(evidence_for) >= _MIN_FOR_TO_CONFIRM and not evidence_against and data_quality_ok:
        state = "CONFIRMED"
    elif len(evidence_for) >= 1:
        # 1-2 corroborating groups (or >=3 but data quality too poor to
        # trust it) is real but insufficient evidence -- not a trade.
        state = "SETUP_FORMING" if data_quality_ok else "WATCH"
    else:
        state = "WATCH"

    if not data_quality_ok:
        confidence = "LOW"
    elif len(evidence_for) >= 4 and not evidence_against and not missing:
        confidence = "HIGH"
    elif len(evidence_for) >= _MIN_FOR_TO_CONFIRM:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "state": state, "confidence_bucket": confidence,
        "evidence_for": evidence_for, "evidence_against": evidence_against,
        "missing_evidence": missing, "no_evidence": no_evidence,
        "groups_evaluated": resolved,
    }
