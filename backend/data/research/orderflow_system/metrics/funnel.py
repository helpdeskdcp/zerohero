"""
Funnel/ablation diagnostic (TRACK 1) -- for each setup, counts how many
bars survive each stage of its lifecycle, and each confirmation
sub-condition's OWN fire rate (not just the AND conjunction). This decides
what, if anything, gets a structural change -- run BEFORE any threshold
widening, never after (spec: "Do NOT widen anything before this runs").

Two setup shapes, two funnel shapes (both real, not toy diagnostics -- they
call the exact same detect_context/detect_trigger/confirm_breakdown/scan
methods the live engine uses, nothing reimplemented):
  - BreakoutSetup: context -> trigger -> {delta, cvd, absorption, vol}
    (individual rates AND sequential AND-narrowing) -> confirmed ->
    entered / invalidated.
  - ReversalSetup: extended_trend -> overshoot -> reclaim (within
    reclaim_max_bars) -> {absorption, divergence, delta_flip, vol} ->
    confirmed -> entered.
"""
from __future__ import annotations

from strategy._breakout import BreakoutSetup
from strategy._reversal import ReversalSetup


def breakout_funnel(df, setup: BreakoutSetup) -> dict:
    sdf = setup.prepare(df)
    n = len(sdf)
    n_context = n_trigger = 0
    cond_counts = {"delta_ok": 0, "cvd_ok": 0, "absorption_ok": 0, "vol_ok": 0}
    seq_counts = {"trigger": 0, "+delta": 0, "+cvd": 0, "+absorption": 0, "+vol(confirmed)": 0}
    n_pending_opened = n_entries = 0

    state = {}
    for i in range(1, n):
        pending_before = state.get("pending") is not None
        if not pending_before:
            if setup.detect_context(sdf, i):
                n_context += 1
                if setup.detect_trigger(sdf, i):
                    n_trigger += 1
                    b = setup.confirm_breakdown(sdf, i)
                    for k, v in b.items():
                        if v:
                            cond_counts[k] += 1
                    seq_counts["trigger"] += 1
                    if b["delta_ok"]:
                        seq_counts["+delta"] += 1
                        if b["cvd_ok"]:
                            seq_counts["+cvd"] += 1
                            if b["absorption_ok"]:
                                seq_counts["+absorption"] += 1
                                if b["vol_ok"]:
                                    seq_counts["+vol(confirmed)"] += 1
        sig = setup.scan(sdf, i, state)
        pending_after = state.get("pending") is not None
        if not pending_before and pending_after:
            n_pending_opened += 1
        if sig is not None:
            n_entries += 1
    n_invalidated = n_pending_opened - n_entries

    return {
        "setup": setup.name_tag, "n_bars": n,
        "context_ok": n_context, "trigger_ok_given_context": n_trigger,
        "condition_individual_fire_rate_given_trigger": {
            k: {"n": v, "rate": round(v / n_trigger, 4) if n_trigger else None} for k, v in cond_counts.items()
        },
        "sequential_and_narrowing_from_trigger": seq_counts,
        "confirmed_count": seq_counts["+vol(confirmed)"],
        "entries": n_entries, "invalidated_after_confirm": n_invalidated,
        "bottleneck_condition": min(cond_counts, key=cond_counts.get) if n_trigger else None,
    }


def reversal_funnel(df, setup: ReversalSetup) -> dict:
    sdf = setup.prepare(df)
    n = len(sdf)
    c = setup.cfg
    n_trend = n_overshoot = n_reclaim_attempted = n_reclaim_ok = 0
    cond_counts = {"absorption_ok": 0, "divergence_ok": 0, "delta_flip_ok": 0, "vol_ok": 0}
    seq_counts = {"overshoot": 0, "+reclaim": 0, "+absorption": 0, "+divergence": 0, "+delta_flip(confirmed)": 0}

    for i in range(setup.structure_cfg["trend_bars"] + 1, n):
        if not bool(sdf["extended_trend_ok"].iloc[i]):
            continue
        n_trend += 1
        if not setup.is_overshoot(sdf, i):
            continue
        n_overshoot += 1
        seq_counts["overshoot"] += 1
        reclaimed_i = None
        for j in range(i + 1, min(i + c["reclaim_max_bars"] + 1, n)):
            n_reclaim_attempted += 1
            if setup.is_reclaim(sdf, j, i):
                reclaimed_i = j
                break
        if reclaimed_i is None:
            continue
        n_reclaim_ok += 1
        seq_counts["+reclaim"] += 1
        b = setup.confirm_breakdown(sdf, reclaimed_i, i)
        for k, v in b.items():
            if v:
                cond_counts[k] += 1
        if b["absorption_ok"]:
            seq_counts["+absorption"] += 1
            if b["divergence_ok"]:
                seq_counts["+divergence"] += 1
                if b["delta_flip_ok"] and b["vol_ok"]:
                    seq_counts["+delta_flip(confirmed)"] += 1

    return {
        "setup": setup.name_tag, "n_bars": n,
        "extended_trend_ok": n_trend, "overshoot_given_trend": n_overshoot,
        "reclaim_ok_given_overshoot": n_reclaim_ok,
        "condition_individual_fire_rate_given_reclaim": {
            k: {"n": v, "rate": round(v / n_reclaim_ok, 4) if n_reclaim_ok else None} for k, v in cond_counts.items()
        },
        "sequential_and_narrowing_from_overshoot": seq_counts,
        "confirmed_count": seq_counts["+delta_flip(confirmed)"],
        "bottleneck_condition": min(cond_counts, key=cond_counts.get) if n_reclaim_ok else None,
    }


def run_funnel(df, setup) -> dict:
    if isinstance(setup, BreakoutSetup):
        return breakout_funnel(df, setup)
    if isinstance(setup, ReversalSetup):
        return reversal_funnel(df, setup)
    raise TypeError(f"unknown setup type: {type(setup)}")
