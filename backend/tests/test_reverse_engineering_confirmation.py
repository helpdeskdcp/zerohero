"""app.reverse_engineering.confirmation -- independent-evidence corroboration."""
from app.reverse_engineering import confirmation as conf


def test_one_strong_group_alone_cannot_reach_confirmed():
    groups = {g: "DATA_UNAVAILABLE" for g in conf.GROUPS}
    groups["price_action"] = "EVIDENCE_FOR"
    out = conf.evaluate_confirmation(groups)
    assert out["state"] != "CONFIRMED"
    assert out["state"] == "SETUP_FORMING" or out["confidence_bucket"] == "LOW"


def test_three_corroborating_groups_with_good_data_quality_confirms():
    groups = {g: "NO_EVIDENCE" for g in conf.GROUPS}
    groups.update({"price_action": "EVIDENCE_FOR", "volume": "EVIDENCE_FOR",
                  "oi": "EVIDENCE_FOR"})
    out = conf.evaluate_confirmation(groups)
    assert out["state"] == "CONFIRMED"
    assert out["confidence_bucket"] in ("MEDIUM", "HIGH")


def test_conflicting_evidence_invalidates_not_averages():
    groups = {g: "NO_EVIDENCE" for g in conf.GROUPS}
    groups.update({"price_action": "EVIDENCE_FOR", "volume": "EVIDENCE_FOR",
                  "oi": "EVIDENCE_FOR", "vwap_trend": "EVIDENCE_AGAINST"})
    out = conf.evaluate_confirmation(groups)
    assert out["state"] == "INVALIDATED"


def test_no_evidence_anywhere_is_no_trade():
    groups = {g: "NO_EVIDENCE" for g in conf.GROUPS}
    out = conf.evaluate_confirmation(groups)
    assert out["state"] == "NO_TRADE"


def test_missing_key_treated_as_data_unavailable_not_no_evidence():
    out = conf.evaluate_confirmation({})
    assert set(out["missing_evidence"]) == set(conf.GROUPS)
    assert out["no_evidence"] == []


def test_poor_data_quality_caps_confirmed_to_watch_even_with_enough_for_groups():
    groups = {g: "DATA_UNAVAILABLE" for g in conf.GROUPS}
    groups.update({"price_action": "EVIDENCE_FOR", "volume": "EVIDENCE_FOR",
                  "oi": "EVIDENCE_FOR"})
    out = conf.evaluate_confirmation(groups)
    assert out["state"] != "CONFIRMED"
    assert out["confidence_bucket"] == "LOW"


def test_invalid_value_falls_back_to_data_unavailable():
    groups = {g: "NO_EVIDENCE" for g in conf.GROUPS}
    groups["price_action"] = "GARBAGE_VALUE"
    out = conf.evaluate_confirmation(groups)
    assert out["groups_evaluated"]["price_action"] == "DATA_UNAVAILABLE"
