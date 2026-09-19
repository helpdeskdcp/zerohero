"""Phase F: instrument profile registry, regime overlay, effective-profile
selection, and SENSEX contamination classification. All pure/deterministic
-- no network, no broker, no live DB writes."""

from app import instrument_profiles as ip
from app import regime_profiles as rp
from app.effective_profile import select_effective_profile
from app.autoscalp import trade_contamination as tc


# ---------------------------------------------------------------- instrument profiles

def test_all_five_watchlist_symbols_have_a_profile():
    for sym in ("NIFTY", "BANKNIFTY", "SENSEX", "NATURALGAS", "CRUDEOIL"):
        prof = ip.get_instrument_profile(sym)
        assert prof.symbol == sym
        assert prof.lot_size.value is not None
        assert prof.lot_size.status == ip.ParamStatus.VERIFIED


def test_nifty_runs_on_unmodified_base_defaults():
    prof = ip.get_instrument_profile("NIFTY")
    assert prof.params == {}
    assert prof.get("sl_atr") == 1.1
    assert prof.get("t1_atr") == 1.7


def test_natgas_and_crudeoil_have_calibrated_overrides_with_real_source():
    ng = ip.get_instrument_profile("NATURALGAS")
    assert ng.get("trail_atr") == 1.6
    assert ng.params["trail_atr"].status == ip.ParamStatus.DERIVED
    assert "2026-09-04" in ng.params["max_hold_sec"].rationale
    cr = ip.get_instrument_profile("CRUDEOIL")
    assert cr.get("sl_atr") == 1.2 and cr.get("t1_atr") == 1.9


def test_natgas_and_crudeoil_cost_model_is_calibrated():
    assert ip.get_instrument_profile("NATURALGAS").cost_model_status == "OK"
    assert ip.get_instrument_profile("CRUDEOIL").cost_model_status == "OK"


def test_nifty_banknifty_sensex_cost_model_is_uncalibrated():
    for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
        assert ip.get_instrument_profile(sym).cost_model_status == "UNCALIBRATED"


def test_sensex_profile_is_marked_unvalidated():
    prof = ip.get_instrument_profile("SENSEX")
    assert prof.validation_status == ip.ParamStatus.UNVALIDATED
    assert "contaminated" in prof.sample_note.lower()


def test_unknown_symbol_falls_back_to_common_defaults_marked_unknown():
    prof = ip.get_instrument_profile("DOGECOIN")
    assert prof.validation_status == ip.ParamStatus.UNKNOWN
    assert prof.params == {}
    assert prof.get("sl_atr") == 1.1     # still gets the safe common default


def test_build_symbol_profiles_config_matches_prior_literal():
    """Locks the exact dict this replaced (app.autoscalp.runner.DEFAULT_CONFIG's
    former symbol_profiles literal) -- byte-identical, single source of truth."""
    prior_literal = {
        "NATURALGAS": {"max_hold_sec": 1800, "ev": {"min_ev_r": 0.15, "rr_min": 1.4},
                       "est_cost_r": 0.10, "trail_atr": 1.6},
        "CRUDEOIL":   {"max_hold_sec": 2400, "ev": {"min_ev_r": 0.15, "rr_min": 1.4},
                       "sl_atr": 1.2, "t1_atr": 1.9, "est_cost_r": 0.10, "trail_atr": 1.6},
    }
    assert ip.build_symbol_profiles_config() == prior_literal


def test_build_symbol_profiles_config_omits_symbols_with_no_overrides():
    out = ip.build_symbol_profiles_config()
    assert "NIFTY" not in out and "BANKNIFTY" not in out and "SENSEX" not in out


def test_registry_wired_into_runner_default_config():
    """The single-source-of-truth wiring: runner.py's actual DEFAULT_CONFIG
    must equal what the registry generates, not a second hardcoded copy."""
    from app.autoscalp import runner as ascr
    assert ascr.DEFAULT_CONFIG["symbol_profiles"] == ip.build_symbol_profiles_config()


# ---------------------------------------------------------------- regime profiles

def test_expiry_day_regime_has_real_overrides():
    reg = rp.get_regime_profile("EXPIRY_DAY")
    assert reg.overrides["max_hold_sec"].value == 480
    assert reg.status == ip.ParamStatus.DERIVED


def test_non_expiry_regimes_are_empty_overrides_not_invented_numbers():
    for regime in ("TRENDING_UP", "TRENDING_DOWN", "RANGE", "HIGH_VOLATILITY", "LOW_VOLATILITY"):
        reg = rp.get_regime_profile(regime)
        assert reg.overrides == {}
        assert reg.status == ip.ParamStatus.DEFAULT


def test_unknown_regime_falls_back_to_normal_day():
    reg = rp.get_regime_profile("SOMETHING_MADE_UP")
    assert reg.regime == "NORMAL_DAY"
    assert reg.overrides == {}


# ---------------------------------------------------------------- effective profile

def test_effective_profile_nifty_normal_day_equals_base():
    eff = select_effective_profile("NIFTY", "RANGE", is_expiry_day=False)
    assert eff.resolved["sl_atr"] == 1.1
    assert eff.resolved["t1_atr"] == 1.7
    assert eff.cost_model_status == "UNCALIBRATED"


def test_effective_profile_natgas_uses_instrument_override():
    eff = select_effective_profile("NATURALGAS", "RANGE", is_expiry_day=False)
    assert eff.resolved["trail_atr"] == 1.6
    assert eff.provenance["trail_atr"]["layer"] == "INSTRUMENT"
    assert eff.cost_model_status == "OK"


def test_effective_profile_expiry_day_overrides_win_over_instrument():
    """On an expiry day, regime overrides must win even for an instrument
    that has its own override for the same param (regime is the more
    specific/urgent context)."""
    eff = select_effective_profile("CRUDEOIL", "TRENDING_UP", is_expiry_day=True)
    assert eff.regime == "EXPIRY_DAY"
    assert eff.resolved["sl_atr"] == 0.9         # expiry override, not CRUDEOIL's 1.2
    assert eff.provenance["sl_atr"]["layer"] == "REGIME"


def test_effective_profile_is_deterministic_and_pure():
    a = select_effective_profile("BANKNIFTY", "TRENDING_DOWN", False)
    b = select_effective_profile("BANKNIFTY", "TRENDING_DOWN", False)
    assert a.to_dict() == b.to_dict()


def test_effective_profile_audit_line_format():
    eff = select_effective_profile("BANKNIFTY", "TRENDING_UP", False)
    line = eff.audit_line()
    assert "PROFILE=BANKNIFTY" in line and "REGIME=TRENDING_UP" in line
    assert "COST=UNCALIBRATED" in line


def test_effective_profile_unknown_symbol_and_regime_still_deterministic():
    eff = select_effective_profile("MADEUP", "MADEUP_REGIME", False)
    assert eff.resolved["sl_atr"] == 1.1
    assert eff.instrument_validation_status == "UNKNOWN"


# ---------------------------------------------------------------- SENSEX contamination

def _sensex_row(entry=100.0, exit_price=100.0, exit_reason="TIME_NODATA",
                opened="2026-09-17T05:00:00+00:00", closed="2026-09-17T05:30:00+00:00",
                market="BSE", status="CLOSED"):
    return {"underlying": "SENSEX", "market": market, "entry": entry, "exit_price": exit_price,
            "exit_reason": exit_reason, "opened_ts": opened, "closed_ts": closed,
            "status": status, "trade_id": "T1"}


def test_classify_contaminated_sensex_row():
    r = tc.classify_trade(_sensex_row())
    assert not r.valid
    assert "TIME_NODATA" in r.reasons
    assert "entry_price_equals_exit_price" in r.reasons
    assert "bse_exchange_routing_contamination" in r.reasons
    assert r.epoch == "PRE_FIX"


def test_classify_valid_row_with_real_price_movement():
    r = tc.classify_trade(_sensex_row(entry=100.0, exit_price=105.0, exit_reason="TARGET"))
    assert r.valid
    assert r.reasons == []


def test_post_fix_epoch_classification():
    r = tc.classify_trade(_sensex_row(opened="2026-09-19T05:00:00+00:00",
                                      closed="2026-09-19T05:30:00+00:00",
                                      entry=100.0, exit_price=105.0, exit_reason="TARGET"))
    assert r.epoch == "POST_FIX"
    assert r.valid


def test_non_bse_symbol_epoch_is_not_applicable():
    r = tc.classify_trade({"underlying": "NIFTY", "market": "NSE", "entry": 100.0,
                           "exit_price": 105.0, "exit_reason": "TARGET",
                           "opened_ts": "2026-09-17T05:00:00+00:00",
                           "closed_ts": "2026-09-17T05:30:00+00:00", "status": "CLOSED"})
    assert r.epoch == "N/A"


def test_data_quality_report_aggregates_correctly():
    rows = [_sensex_row() for _ in range(3)] + [
        tc_row for tc_row in [dict(_sensex_row(entry=100.0, exit_price=110.0, exit_reason="TARGET",
                                               opened="2026-09-19T05:00:00+00:00",
                                               closed="2026-09-19T05:30:00+00:00"))]]
    report = tc.data_quality_report(rows)
    assert report["total_rows"] == 4
    assert report["contaminated_rows"] == 3
    assert report["valid_rows"] == 1
    assert report["bse_pre_fix_count"] == 3
    assert report["bse_post_fix_count"] == 1
    assert report["exclusion_reasons"]["TIME_NODATA"] == 3


def test_data_quality_report_never_silently_drops_rows():
    rows = [_sensex_row(), _sensex_row()]
    report = tc.data_quality_report(rows)
    assert len(report["excluded_trade_ids"]) == report["contaminated_rows"]
    assert len(report["detail"]) == report["contaminated_rows"]
