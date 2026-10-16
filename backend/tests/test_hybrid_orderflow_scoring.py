import copy

import pytest

from app.hybrid_orderflow import scoring as S
from app.hybrid_orderflow.policy import PolicyError, load_policy, validate

P = load_policy()
OK = {"symbol": "NIFTY", "now_hhmm_ist": "10:30", "kill_switch": False, "data_stale": False, "last_signal": None}


def ev(component, direction, points, data_class=S.INFERRED):
    return S.Evidence(component, direction, points, data_class)


def full_bull():
    return [ev("structure", S.BULL, 2), ev("liquidity_sweep", S.BULL, 2), ev("delta_cvd", S.BULL, 2), ev("imbalance_absorption", S.BULL, 2),
            ev("vwap_regime", S.BULL, 1), ev("relative_volume", None, 1), ev("htf_alignment", S.BULL, 1)]


# ---- scoring core ----------------------------------------------------------------------------------------------------------
def test_full_confluence_signals_and_correlation_caps_apply():
    r = S.score(full_bull(), OK, P)
    # STRUCTURE 2 + LIQUIDITY 2 + ORDERFLOW min(4,3)=3 + CONTEXT min(2,1)=1 + PARTICIPATION 1 (direction-neutral: adds points, is not a directional group)
    assert r["status"] == "SIGNAL" and r["direction"] == S.BULL and r["total"] == 9 and r["distinct_groups"] == 4
    assert r["groups"]["ORDERFLOW"] == {"raw": 4.0, "counted": 3.0, "cap": 3} and r["groups"]["CONTEXT"]["counted"] == 1
    assert r["is_probability"] is False and r["validation_status"] == "DRAFT_UNVALIDATED"


def test_correlated_orderflow_alone_cannot_reach_the_threshold():
    r = S.score([ev("structure", S.BULL, 2), ev("delta_cvd", S.BULL, 2), ev("imbalance_absorption", S.BULL, 2)], OK, P)
    assert r["status"] == "WAIT" and r["total"] == 5 and "SCORE_BELOW_MIN" in r["reasons"] and "INSUFFICIENT_EVIDENCE_GROUPS" in r["reasons"]


def test_opposing_evidence_forces_wait_even_with_a_high_score():
    e = full_bull()
    e[-1] = ev("htf_alignment", S.BEAR, 1)
    r = S.score(e, OK, P)
    assert r["status"] == "WAIT" and "CONFLICTING_EVIDENCE" in r["reasons"] and r["opposing"] == ["htf_alignment"]


def test_missing_mandatory_structure_is_wait():
    r = S.score([x for x in full_bull() if x.component != "structure"], OK, P)
    assert r["status"] == "WAIT" and "MANDATORY_COMPONENT_MISSING" in r["reasons"]


def test_bearish_mirror_and_points_are_clipped_to_component_max():
    e = [ev(c.component, S.BEAR if c.direction == S.BULL else None, 9) for c in full_bull()]
    r = S.score(e, OK, P)
    assert r["direction"] == S.BEAR and r["status"] == "SIGNAL" and all(c["points"] <= P["scoring"]["components_max"][c["component"]] for c in r["components"])


def test_no_evidence_and_exact_tie():
    assert S.score([], OK, P)["reasons"] == ["NO_EVIDENCE"]
    tie = S.score([ev("structure", S.BULL, 2), ev("liquidity_sweep", S.BEAR, 2)], OK, P)
    assert tie["status"] == "WAIT" and tie["reasons"] == ["CONFLICTING_EVIDENCE"]


@pytest.mark.parametrize("ctx_patch,reason", [({"kill_switch": True}, "KILL_SWITCH"), ({"kill_switch": None}, "KILL_SWITCH"), ({"data_stale": True}, "DATA_STALE"),
                                              ({"data_stale": None}, "DATA_STALE"), ({"now_hhmm_ist": "09:16"}, "OUTSIDE_SESSION"), ({"now_hhmm_ist": "15:01"}, "OUTSIDE_SESSION"),
                                              ({"symbol": "RELIANCE"}, "UNSUPPORTED_INSTRUMENT")])
def test_mandatory_gates_fail_closed_to_no_trade(ctx_patch, reason):
    r = S.score(full_bull(), {**OK, **ctx_patch}, P)
    assert r["status"] == "NO_TRADE" and reason in r["reasons"]


def test_unknown_kill_switch_state_fails_closed_when_absent():
    ctx = {k: v for k, v in OK.items() if k != "kill_switch"}
    assert S.score(full_bull(), ctx, P)["status"] == "NO_TRADE"


def test_mcx_session_window_differs():
    assert S.score(full_bull(), {**OK, "symbol": "NATURALGAS", "now_hhmm_ist": "20:00"}, P)["status"] == "SIGNAL"
    assert S.score(full_bull(), {**OK, "symbol": "NIFTY", "now_hhmm_ist": "20:00"}, P)["status"] == "NO_TRADE"


def test_duplicate_signal_window():
    assert S.score(full_bull(), {**OK, "last_signal": {"direction": S.BULL, "age_sec": 60}}, P)["reasons"] == ["DUPLICATE_SIGNAL"]
    assert S.score(full_bull(), {**OK, "last_signal": {"direction": S.BULL, "age_sec": 301}}, P)["status"] == "SIGNAL"
    assert S.score(full_bull(), {**OK, "last_signal": {"direction": S.BEAR, "age_sec": 60}}, P)["status"] == "SIGNAL"


# ---- adapters ---------------------------------------------------------------------------------------------------------------
def test_structure_wick_only_is_not_structure_and_old_events_expire():
    wick = S.structure_evidence([{"type": "BOS", "direction": S.BULL, "bar_index": 10, "close_confirmed": False}], 11, P)
    assert wick.points == 0 and wick.reasons == ["WICK_ONLY_NOT_STRUCTURE"]
    assert S.structure_evidence([{"type": "BOS", "direction": S.BULL, "bar_index": 1, "close_confirmed": True}], 20, P).reasons == ["NO_CONFIRMED_STRUCTURE"]
    bos = S.structure_evidence([{"type": "BOS", "direction": S.BULL, "bar_index": 10, "close_confirmed": True}], 12, P)
    choch = S.structure_evidence([{"type": "CHOCH", "direction": S.BEAR, "bar_index": 10, "close_confirmed": True}], 12, P)
    assert (bos.points, bos.direction, choch.points, choch.direction) == (2, S.BULL, 1, S.BEAR)
    future = S.structure_evidence([{"type": "BOS", "direction": S.BULL, "bar_index": 15, "close_confirmed": True}], 12, P)
    assert future.points == 0                                           # an event confirmed AFTER now is never used


def test_sweep_direction_speed_and_age():
    up = S.sweep_evidence({"kind": "UPPER_SWEEP", "reclaim_bar_index": 10, "bars_to_reclaim": 1}, 11, P)
    lo = S.sweep_evidence({"kind": "LOWER_SWEEP", "reclaim_bar_index": 10, "bars_to_reclaim": 3}, 11, P)
    assert (up.direction, up.points, lo.direction, lo.points) == (S.BEAR, 2, S.BULL, 1)
    assert S.sweep_evidence(None, 11, P).reasons == ["NO_CONFIRMED_SWEEP"]
    assert S.sweep_evidence({"kind": "UPPER_SWEEP", "reclaim_bar_index": 1, "bars_to_reclaim": 1}, 50, P).reasons == ["SWEEP_TOO_OLD"]


def test_delta_rules():
    assert S.delta_cvd_evidence(0.9, 1.0, 0.95, "OHLC_PROXY", P).reasons == ["FORBIDDEN_PROXY_AS_DELTA"]
    assert S.delta_cvd_evidence(0.9, 1.0, 0.5, S.INFERRED, P).reasons == ["LOW_CLASSIFIED_FRACTION"]
    assert S.delta_cvd_evidence(None, 1.0, 0.9, S.INFERRED, P).reasons == ["DATA_MISSING"]
    assert S.delta_cvd_evidence(0.1, 1.0, 0.9, S.INFERRED, P).points == 0
    one = S.delta_cvd_evidence(0.35, 1.0, 0.9, S.INFERRED, P)
    two = S.delta_cvd_evidence(0.6, 2.0, 0.9, S.INFERRED, P)
    nocvd = S.delta_cvd_evidence(0.6, -1.0, 0.9, S.INFERRED, P)              # CVD disagrees -> only 1 point
    assert (one.points, two.points, nocvd.points) == (1, 2, 1) and nocvd.reasons == ["CVD_NOT_CONFIRMING"]
    assert S.delta_cvd_evidence(-0.6, -2.0, 0.9, S.INFERRED, P).direction == S.BEAR


def test_imbalance_requires_verified_adjacency():
    runs = [{"direction": "BUY", "levels": 3, "adjacency_verified": False}]
    assert S.imbalance_evidence(runs, P).reasons == ["ADJACENCY_UNVERIFIED"]
    assert S.imbalance_evidence([{"direction": "SELL", "levels": 3, "adjacency_verified": True}], P).points == 1
    big = S.imbalance_evidence([{"direction": "BUY", "levels": 4, "adjacency_verified": True}], P)
    assert (big.points, big.direction) == (2, S.BULL) and S.imbalance_evidence([], P).points == 0


def test_vwap_rvol_htf_adapters():
    assert S.vwap_evidence(100, None, S.BULL, P).reasons == ["VWAP_UNAVAILABLE"]
    assert S.vwap_evidence(101, 100, S.BULL, P).points == 1 and S.vwap_evidence(101, 100, S.BEAR, P).points == 0
    assert S.rvol_evidence(1.6, P).points == 1 and S.rvol_evidence(1.0, P).points == 0 and S.rvol_evidence(None, P).reasons == ["RVOL_UNAVAILABLE"]
    assert S.htf_evidence(S.BULL, False, P).reasons == ["HTF_BAR_NOT_COMPLETED"] and S.htf_evidence(S.BULL, True, P).points == 1


def test_end_to_end_from_adapters():
    e = [S.structure_evidence([{"type": "BOS", "direction": S.BULL, "bar_index": 10, "close_confirmed": True}], 11, P),
         S.sweep_evidence({"kind": "LOWER_SWEEP", "reclaim_bar_index": 9, "bars_to_reclaim": 1}, 11, P),
         S.delta_cvd_evidence(0.55, 3.0, 0.9, S.INFERRED, P),
         S.imbalance_evidence([{"direction": "BUY", "levels": 4, "adjacency_verified": True}], P),
         S.vwap_evidence(None, None, None, P), S.rvol_evidence(2.0, P), S.htf_evidence(S.BULL, True, P)]
    r = S.score(e, OK, P)
    assert r["status"] == "SIGNAL" and r["total"] == 9 - 0 and any(c["component"] == "vwap_regime" and "VWAP_UNAVAILABLE" in c["reasons"] for c in r["components"])


# ---- policy validation of the new sections ----------------------------------------------------------------------------------
@pytest.mark.parametrize("mutate,needle", [
    (lambda p: p["scoring"]["groups"]["ORDERFLOW"].remove("delta_cvd"), "groups must cover"),
    (lambda p: p["scoring"]["evidence_rules"].pop("htf_alignment"), "evidence_rules"),
    (lambda p: p["scoring"]["evidence_rules"]["delta_cvd"].__setitem__("delta_pct_1pt", 0.9), "delta_cvd thresholds"),
    (lambda p: p["scoring"]["evidence_rules"]["structure"].__setitem__("BOS", 5), "structure points"),
    (lambda p: p["scoring"].__setitem__("duplicate_window_sec", 0), "duplicate_window_sec"),
    (lambda p: p["scoring"].__setitem__("mandatory_components", []), "mandatory_components"),
    (lambda p: p["scoring"]["evidence_rules"]["imbalance_absorption"].__setitem__("require_adjacency_verified", False), "adjacency"),
])
def test_scoring_policy_sections_fail_closed(mutate, needle):
    p = copy.deepcopy(P)
    mutate(p)
    with pytest.raises(PolicyError, match=needle):
        validate(p)
