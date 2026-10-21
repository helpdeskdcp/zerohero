"""Expiry resolver: metadata precedence, versioned rules, calendars, and
NO-FABRICATION guards (spec sections 19, 23)."""
from datetime import date

import pytest

from app.hist_options import expiry as E
from app.hist_options.expiry_store import ExpiryAudit

CAL_DAYS = [date(2025, 10, 16), date(2025, 10, 17), date(2025, 10, 20), date(2025, 10, 21), date(2025, 10, 22)]


@pytest.fixture(scope="module")
def R():
    return E.ExpiryResolver()


def test_nifty_weekly_thursday_before_sep_2025_from_metadata(R):
    r = R.resolve("NIFTY", date(2025, 8, 20))
    assert (r.expiry_date, r.expiry_status, r.expiry_source) == ("2025-08-21", "VERIFIED", "BROKER_METADATA")
    assert date.fromisoformat(r.expiry_date).weekday() == 3 and r.rule_version == "NIFTY_WEEKLY_THU_v1"


def test_nifty_weekly_tuesday_from_sep_2025(R):
    r = R.resolve("NIFTY", date(2025, 9, 3))
    assert r.expiry_date == "2025-09-09" and date.fromisoformat(r.expiry_date).weekday() == 1


def test_nifty_last_thursday_aug_and_first_tuesday_sep_boundary(R):
    assert R.resolve("NIFTY", date(2025, 8, 28)).expiry_date == "2025-08-28"
    assert R.resolve("NIFTY", date(2025, 8, 29)).expiry_date == "2025-09-02"


def test_tuesday_holiday_moves_to_previous_trading_day(R):
    r = R.resolve("NIFTY", date(2026, 4, 8))                         # Tue 14-Apr-2026 holiday -> Mon 13
    assert r.expiry_date == "2026-04-13" and r.holiday_adjustment == "PREV_TRADING_DAY" and not r.expiry_rule_conflict


def test_metadata_wins_over_rule_and_conflict_is_audited(R, tmp_path):
    r = R.resolve("NIFTY", date(2025, 10, 17))                       # muhurat day makes observed calendar disagree
    assert r.expiry_date == r.metadata_expiry == "2025-10-20"
    assert r.calculated_expiry == "2025-10-21" and r.expiry_rule_conflict is True
    a = ExpiryAudit(str(tmp_path / "a.db")); a.log("2025-10-17", r)
    c = a.conflicts()
    assert len(c) == 1 and c[0]["metadata_expiry"] == "2025-10-20" and c[0]["calculated_expiry"] == "2025-10-21"


def test_banknifty_never_generates_weekly_after_2024_11(R):
    for d in (date(2025, 3, 3), date(2026, 1, 5)):
        r = R.resolve("BANKNIFTY", d, flag="WEEK")
        assert r.expiry_status == "UNAVAILABLE" and r.reason.startswith("NO_WEEKLY_CONTRACTS")
    m = R.resolve("BANKNIFTY", date(2025, 3, 3), flag="MONTH")
    assert m.expiry_date == "2025-03-27" and date.fromisoformat(m.expiry_date).weekday() == 3
    assert R.resolve("BANKNIFTY", date(2026, 3, 2), flag="MONTH").expiry_date == "2026-03-30"    # Tue 31 holiday -> Mon 30


def test_sensex_rule_versions_follow_broker_metadata_not_a_single_rule(R):
    assert date.fromisoformat(R.resolve("SENSEX", date(2024, 11, 20)).expiry_date).weekday() == 4     # Friday
    assert date.fromisoformat(R.resolve("SENSEX", date(2025, 3, 5)).expiry_date).weekday() == 1       # Tuesday
    assert date.fromisoformat(R.resolve("SENSEX", date(2026, 3, 6)).expiry_date).weekday() == 3       # Thursday
    assert R.resolve("SENSEX", date(2025, 1, 2)).expiry_date == "2025-01-03"                          # last Friday


def test_sensex_uses_bse_calendar_not_nse():
    nse = E.Calendar("NSE", [date(2025, 1, 2), date(2025, 1, 3)])
    bse = E.Calendar("BSE", [date(2025, 1, 2)])
    R = E.ExpiryResolver(metadata={}, calendars={"NSE": nse, "BSE": bse}, allow_unverified_rules=True)
    assert R.cals["BSE"] is bse and E.EXCHANGE["SENSEX"] == "BSE" and E.EXCHANGE["NIFTY"] == "NSE"


def test_unverified_period_is_unavailable_not_guessed(R):
    r = R.resolve("NIFTY", date(2023, 6, 7))
    assert r.expiry_date is None and r.expiry_status == "UNAVAILABLE" and r.reason == "RULE_EVIDENCE_NOT_VERIFIED_FOR_THIS_PERIOD"


def test_unverified_rule_is_labelled_reconstructed_and_user_spec_only_on_opt_in():
    r = E.ExpiryResolver(allow_unverified_rules=True).resolve("NIFTY", date(2023, 6, 7))
    assert r.expiry_status == "RECONSTRUCTED" and r.rule_evidence == "USER_SPEC_UNVERIFIED" and r.expiry_source == "RULE_RECONSTRUCTION"


def test_missing_calendar_coverage_is_unavailable_never_nearest(R):
    r = E.ExpiryResolver(metadata={}, calendars={"NSE": E.Calendar("NSE", CAL_DAYS)}).resolve("NIFTY", date(2025, 10, 16))
    assert r.expiry_status == "UNAVAILABLE"


def test_sensex_before_bse_calendar_coverage_is_unavailable(R):
    r = E.ExpiryResolver(allow_unverified_rules=True).resolve("SENSEX", date(2023, 5, 10))
    assert r.expiry_status == "UNAVAILABLE" and r.expiry_date is None


def test_unknown_underlying_and_mcx_history_unavailable(R):
    assert R.resolve("UNKNOWN", date(2025, 1, 1)).reason == "UNKNOWN_UNDERLYING"
    assert R.resolve("GOLD", date(2025, 1, 1)).reason == "CALENDAR_EXCHANGE_DISABLED"
    for u in ("NATURALGAS", "NATGASMINI", "CRUDEOIL", "CRUDEOILM"):
        r = R.resolve(u, date(2025, 1, 1))
        assert r.exchange == "MCX" and r.expiry_status == "UNAVAILABLE" and r.expiry_date is None


def test_mcx_needs_mcx_calendar_never_nse_bse_or_weekdays():
    nse = E.load_observed_calendars()["NSE"]
    assert E.mcx_option_expiry("2026-10-19", None, underlying="CRUDEOIL").reason == "CALENDAR_EXCHANGE_DISABLED"
    assert E.mcx_option_expiry("2026-10-19", nse, underlying="CRUDEOIL").reason == "CALENDAR_EXCHANGE_DISABLED"


def test_mcx_two_business_days_before_futures_expiry_with_mcx_calendar():
    days = [date(2026, 10, d) for d in (12, 13, 14, 15, 16, 19)]
    cal = E.Calendar("MCX", days)
    r = E.mcx_option_expiry("2026-10-19", cal, underlying="CRUDEOIL")
    assert r.expiry_date is None and r.expiry_status == "UNAVAILABLE" and r.reason == "CALENDAR_EXCHANGE_DISABLED"
    cal2 = E.Calendar("MCX", [d for d in days if d != date(2026, 10, 16)])          # 16-Oct MCX holiday
    assert E.mcx_option_expiry("2026-10-19", cal2, underlying="CRUDEOIL").expiry_date is None
    short = E.Calendar("MCX", [date(2026, 10, 19)])
    assert E.mcx_option_expiry("2026-10-19", short).expiry_status == "UNAVAILABLE"


def test_dhan_expiry_list_is_dhan_metadata_and_mini_resolved_independently():
    a = E.from_dhan_expiry_list("CRUDEOIL", "2026-09-30", ["2026-10-15", "2026-11-17"])
    b = E.from_dhan_expiry_list("CRUDEOILM", "2026-09-30", ["2026-10-19"])
    assert (a.expiry_date, a.expiry_status, a.reason) == (None, "UNAVAILABLE", "CALENDAR_EXCHANGE_DISABLED")
    assert b.expiry_date is None and b.reason == "CALENDAR_EXCHANGE_DISABLED"
    assert E.from_dhan_expiry_list("NIFTY", "2026-09-30", ["2026-10-06"]).expiry_date == "2026-10-06"
    assert E.from_dhan_expiry_list("CRUDEOIL", "2027-01-01", ["2026-10-15"]).expiry_status == "UNAVAILABLE"


def test_no_silent_nearest_expiry_when_beyond_metadata_and_calendar(R):
    r = R.resolve("NIFTY", date(2031, 1, 1))
    assert r.expiry_date is None and r.expiry_status == "UNAVAILABLE"


def test_every_metadata_expiry_resolves_verified_from_broker(R):
    meta = E.load_metadata()
    for u in ("NIFTY", "SENSEX"):
        for x in meta[u][1:-1]:
            r = R.resolve(u, x)
            assert r.expiry_date == x.isoformat() and r.expiry_source == "BROKER_METADATA" and r.expiry_status == "VERIFIED"


@pytest.mark.parametrize("symbol,bar_date,code,expected", [
    ("NIFTY", "2025-04-08", 1, "2025-04-24"),
    ("NIFTY", "2025-04-25", 1, "2025-05-29"),
    ("NIFTY", "2025-04-08", 2, "2025-05-29"),
    ("SENSEX", "2024-10-28", 1, "2024-11-29"),
    ("BANKNIFTY", "2024-12-02", 1, "2024-12-24"),
    ("BANKNIFTY", "2026-03-02", 1, "2026-03-30"),
])
def test_monthly_contract_uses_nominal_month_not_last_listed_date(R, symbol, bar_date, code, expected):
    r = R.resolve(symbol, bar_date, flag="MONTH", code=code)
    assert r.expiry_date == expected and r.expiry_status == "VERIFIED"


def test_monthly_classification_missing_calendar_is_unavailable():
    r = E.ExpiryResolver(calendars={}).resolve("NIFTY", "2025-04-08", flag="MONTH")
    assert r.expiry_status == "UNAVAILABLE" and r.expiry_date is None


def test_monthly_does_not_skip_ambiguous_month_to_return_a_later_contract():
    md = E.load_metadata()
    md["NIFTY"] = [d for d in md["NIFTY"] if d != date(2025, 4, 24)]
    r = E.ExpiryResolver(metadata=md).resolve("NIFTY", "2025-04-08", flag="MONTH")
    assert r.expiry_status == "UNAVAILABLE" and r.expiry_date is None


def test_monthly_classification_does_not_invent_a_rule_for_untagged_metadata():
    r = E.ExpiryResolver().resolve("MIDCPNIFTY", "2024-10-01", flag="MONTH")
    assert r.expiry_status == "UNAVAILABLE"


def test_sensex_missing_monthly_metadata_is_not_replaced_with_shifted_weekly(R):
    r = R.resolve("SENSEX", "2024-10-04", flag="MONTH")
    assert r.expiry_status == "UNAVAILABLE" and r.reason == "MONTHLY_METADATA_RULE_CONFLICT"
    assert r.calculated_expiry == "2024-10-25"


def test_sensex_month_boundary_with_complete_synthetic_metadata():
    md = {"SENSEX": [date(2024, 10, d) for d in (4, 11, 18, 25, 31)]}
    r = E.ExpiryResolver(metadata=md).resolve("SENSEX", "2024-10-04", flag="MONTH")
    assert r.expiry_date == "2024-10-25" and r.expiry_status == "VERIFIED"


def test_monthly_rule_only_retains_reconstructed_provenance():
    r = E.ExpiryResolver(metadata={}).resolve("NIFTY", "2025-04-08", flag="MONTH")
    assert (r.expiry_date, r.expiry_status, r.expiry_source) == ("2025-04-24", "RECONSTRUCTED", "RULE_RECONSTRUCTION")


@pytest.mark.parametrize("allow", [False, True])
def test_monthly_unverified_rule_requires_opt_in(allow):
    r = E.ExpiryResolver(allow_unverified_rules=allow).resolve("NIFTY", "2023-06-07", flag="MONTH")
    assert r.expiry_status == ("RECONSTRUCTED" if allow else "UNAVAILABLE")
    if allow:
        assert r.rule_evidence == "USER_SPEC_UNVERIFIED"
