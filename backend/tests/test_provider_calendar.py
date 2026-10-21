"""Synthetic evidence tests resolver contracts, never real exchange calendars."""
from datetime import datetime, timezone
from dataclasses import replace
import json

import pytest

from app.histcap.read_contract import resolve_expiry
from app.hist_options.provider_calendar import ProviderCalendars, resolve, identity
from calendar_fixtures import synthetic_evidence, synthetic_calendars

AT = datetime(2026, 9, 2, 4, 30, tzinfo=timezone.utc)


def test_unverified_date_list_cannot_authorize_historical_auto():
    """A provider name plus dates does not prove historical calendar validity."""
    with pytest.raises(ValueError, match="CALENDAR_EVIDENCE_UNAVAILABLE"):
        resolve_expiry("AUTO", "DHAN", datetime(2026, 9, 2, tzinfo=timezone.utc),
                       {"DHAN": ["08SEP2026"]})


def test_explicit_date_does_not_require_or_claim_calendar_verification():
    """Explicit owner/caller contract identity remains authoritative."""
    assert resolve_expiry("08SEP2026", "DHAN", datetime(2026, 9, 2, tzinfo=timezone.utc),
                          None).isoformat() == "2026-09-08"


@pytest.mark.parametrize("symbol", ["NIFTY", "NIFTY 50", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
    "SENSEX", "BANKEX", "NATGAS", "NATURALGAS", "NATGASMINI", "CRUDEOIL", "CRUDEOILM",
    "CRUDEOILMINI", "GOLD", "GOLDMINI", "SILVER", "SILVERMINI"])
@pytest.mark.parametrize("provider", ["DHAN", "ANGELONE"])
@pytest.mark.parametrize("alias,expected", [("AUTO", "2026-09-08"), ("NEXT", "2026-09-15")])
def test_provider_instrument_scoped_historical_aliases(symbol, provider, alias, expected):
    """Same algorithm accepts evidence for every registered identity, without date rules."""
    calendars = synthetic_calendars({provider: ["08SEP2026", "15SEP2026"]}, underlying=symbol)
    if identity(symbol)[1] == "MCX":
        with pytest.raises(ValueError, match="CALENDAR_EXCHANGE_DISABLED"):
            resolve(alias, provider, symbol, AT, calendars)
        return  # Owner-disabled scope, regardless of reviewed evidence.
    expiry, provenance = resolve(alias, provider, symbol, AT, calendars)
    assert expiry.isoformat() == expected
    assert provenance["provider"] == provider
    assert (provenance["underlying"], provenance["exchange"]) == identity(symbol)
    assert provenance["calendar_version"] == "synthetic-v1"
    assert provenance["source"].startswith("test-fixture:")
    assert provenance["manifest_sha256"] == calendars.evidence[0].manifest_sha256


@pytest.mark.parametrize("alias", ["AUTO", "NEXT", "CURRENT", "LATEST"])
@pytest.mark.parametrize("evidence", [None, {}, {"DHAN": ["08SEP2026"]}, ProviderCalendars()])
def test_missing_or_unreviewed_evidence_fails_closed(alias, evidence):
    """Missing evidence is never reconstructed from today's rules or captured subsets."""
    with pytest.raises(ValueError, match="CALENDAR_EVIDENCE_UNAVAILABLE"):
        resolve(alias, "DHAN", "NIFTY", AT, evidence)


@pytest.mark.parametrize("override", [dict(provider="ANGELONE"), dict(underlying="BANKNIFTY"),
                                     dict(instrument="FUTURE")])
def test_other_identity_never_satisfies_option_calendar(override):
    """Provider, underlying and instrument are independent mandatory identity dimensions."""
    calendars = ProviderCalendars((synthetic_evidence(**override),))
    with pytest.raises(ValueError, match="CALENDAR_EVIDENCE_UNAVAILABLE"):
        resolve("AUTO", "DHAN", "NIFTY", AT, calendars)


@pytest.mark.parametrize("override,reason", [
    (dict(complete=False), "CALENDAR_INCOMPLETE"),
    (dict(schema_version="unknown-v9"), "CALENDAR_SCHEMA_UNSUPPORTED"),
    (dict(calendar_version=""), "CALENDAR_PROVENANCE_MISSING"),
    (dict(source=""), "CALENDAR_PROVENANCE_MISSING"),
    (dict(effective_to="2026-08-31T00:00:00Z"), "CALENDAR_INTERVAL_INVALID"),
    (dict(effective_from="2026-09-01"), "CALENDAR_EVIDENCE_INVALID"),
    (dict(entries=[dict(expiry="2026-02-30")]), "INVALID_EXPIRY"),
])
def test_invalid_calendar_never_authorizes_selection(override, reason):
    """Invalid, incomplete or unsupported evidence is explicitly unavailable."""
    with pytest.raises(ValueError, match=reason):
        resolve("AUTO", "DHAN", "NIFTY", AT, ProviderCalendars((synthetic_evidence(**override),)))


@pytest.mark.parametrize("override,reason", [
    (dict(verification_status="UNVERIFIED"), "CALENDAR_NOT_VERIFIED"),
    (dict(verified_by=""), "CALENDAR_REVIEW_MISSING"),
    (dict(verification_reference=""), "CALENDAR_REVIEW_MISSING"),
    (dict(manifest=b"tampered"), "CALENDAR_EVIDENCE_UNAVAILABLE"),
    (dict(manifest_sha256="0" * 64), "CALENDAR_MANIFEST_HASH_MISMATCH"),
    (dict(source_artifact=b"tampered"), "CALENDAR_SOURCE_HASH_MISMATCH"),
])
def test_review_and_retained_evidence_integrity(override, reason):
    """A VERIFIED label cannot replace retained source, integrity and review metadata."""
    e = replace(synthetic_evidence(), **override)
    with pytest.raises(ValueError, match=reason):
        resolve("AUTO", "DHAN", "NIFTY", AT, ProviderCalendars((e,)))


@pytest.mark.parametrize("override", [dict(published_at="2026-09-03T00:00:00Z"),
    dict(effective_from="2026-09-03T00:00:00Z"), dict(effective_to="2026-09-02T04:30:00Z")])
def test_future_or_out_of_range_calendar_never_answers_asof(override):
    """Published-at and half-open effective interval both constrain historical evidence."""
    with pytest.raises(ValueError, match="CALENDAR_EVIDENCE_UNAVAILABLE"):
        resolve("AUTO", "DHAN", "NIFTY", AT, ProviderCalendars((synthetic_evidence(**override),)))


@pytest.mark.parametrize("at,expected", [("2026-09-08T09:59:59Z", "2026-09-08"),
    ("2026-09-08T15:30:00+05:30", "2026-09-15"), ("2026-09-08T10:00:00Z", "2026-09-15")])
def test_last_trading_boundary_comes_from_evidence(at, expected):
    """At the documented cutoff an expiring contract cannot satisfy AUTO."""
    expiry, _ = resolve("AUTO", "DHAN", "NIFTY", datetime.fromisoformat(at),
                        ProviderCalendars((synthetic_evidence(),)))
    assert expiry.isoformat() == expected


def test_mcx_cutoff_is_not_inferred_from_nse_hours():
    """MCX cutoff is supplied by evidence, never copied from index session rules."""
    e = synthetic_evidence(underlying="NATGASMINI")
    entries = json.loads(e.manifest)["entries"]
    entries[0]["last_trading_at"] = "2026-09-08T23:55:00+05:30"
    calendars = ProviderCalendars((synthetic_evidence(underlying="NATGASMINI", entries=entries),))
    for at, expected in [("2026-09-08T15:30:00+05:30", "2026-09-08"),
                         ("2026-09-08T23:55:00+05:30", "2026-09-15")]:
        with pytest.raises(ValueError, match="CALENDAR_EXCHANGE_DISABLED"):
            resolve("AUTO", "DHAN", "NATGASMINI", datetime.fromisoformat(at), calendars)


def test_contract_listed_later_is_not_historical_next():
    """Even a known future listing cannot be traded/selected before its listing time."""
    entries = json.loads(synthetic_evidence().manifest)["entries"]
    entries[1]["listed_at"] = "2026-09-10T09:00:00+05:30"
    calendars = ProviderCalendars((synthetic_evidence(entries=entries),))
    assert resolve("AUTO", "DHAN", "NIFTY", AT, calendars)[0].isoformat() == "2026-09-08"
    with pytest.raises(ValueError, match="CALENDAR_EXPIRY_UNAVAILABLE"):
        resolve("NEXT", "DHAN", "NIFTY", AT, calendars)


def test_calendar_validity_boundary_uses_instant_not_utc_date():
    """The IST midnight boundary is the same instant when represented in UTC."""
    calendars = ProviderCalendars((synthetic_evidence(),))
    for ts in ("2026-08-31T18:30:00Z", "2026-09-01T00:00:00+05:30"):
        assert resolve("AUTO", "DHAN", "NIFTY", datetime.fromisoformat(ts), calendars)[0].isoformat() == "2026-09-08"
    with pytest.raises(ValueError, match="CALENDAR_EVIDENCE_UNAVAILABLE"):
        resolve("AUTO", "DHAN", "NIFTY", datetime.fromisoformat("2026-08-31T18:29:59Z"), calendars)
    with pytest.raises(ValueError, match="CALENDAR_TIMEZONE_REQUIRED"):
        resolve("AUTO", "DHAN", "NIFTY", datetime(2026, 9, 2), calendars)


def test_holiday_shift_and_rule_change_use_explicit_versioned_evidence():
    """Historical shifted dates are facts in evidence, never recomputed from current weekdays."""
    old = synthetic_evidence(expiries=["07SEP2026", "14SEP2026"], effective_to="2026-09-05T00:00:00Z")
    new = synthetic_evidence(calendar_version="synthetic-v2", published_at="2026-09-05T00:00:00Z",
                             effective_from="2026-09-05T00:00:00Z")
    calendars = ProviderCalendars((new, old))
    exp, p = resolve("AUTO", "DHAN", "NIFTY", AT, calendars)
    assert exp.isoformat() == "2026-09-07" and p["calendar_version"] == "synthetic-v1"
    exp, p = resolve("AUTO", "DHAN", "NIFTY", datetime.fromisoformat("2026-09-05T00:00:00Z"), calendars)
    assert exp.isoformat() == "2026-09-08" and p["calendar_version"] == "synthetic-v2"


def test_overlapping_versions_are_unavailable_not_arbitrarily_selected():
    """Conflicting manifests cannot be resolved by tuple/database insertion order."""
    calendars = ProviderCalendars((synthetic_evidence(), synthetic_evidence(calendar_version="synthetic-v2")))
    with pytest.raises(ValueError, match="CALENDAR_VERSION_CONFLICT"):
        resolve("AUTO", "DHAN", "NIFTY", AT, calendars)


def test_future_unreviewed_version_cannot_change_past_resolution():
    """Adding later evidence cannot change the result of an earlier as-of read."""
    old = synthetic_evidence()
    future = replace(synthetic_evidence(calendar_version="synthetic-v2", published_at="2026-09-03T00:00:00Z"),
                     verification_status="UNVERIFIED")
    expected = resolve("AUTO", "DHAN", "NIFTY", AT, ProviderCalendars((old,)))
    assert resolve("AUTO", "DHAN", "NIFTY", AT, ProviderCalendars((future, old))) == expected


def test_unknown_alias_cannot_default_to_auto():
    with pytest.raises(ValueError, match="CALENDAR_ALIAS_INVALID"):
        ProviderCalendars((synthetic_evidence(),)).resolve("TYPO", "DHAN", "NIFTY", AT)


@pytest.mark.parametrize("expiry", ["2026-09-01", "2026-09-08", "2026-10-08"])
def test_explicit_dates_remain_authoritative_even_outside_calendar(expiry):
    """Explicit identity is preserved; quote validity/Greek expiry guards remain downstream."""
    actual, provenance = resolve(expiry, "DHAN", "NIFTY", AT, {"ANGELONE": ["15SEP2026"]})
    assert actual.isoformat() == expiry
    assert provenance["verification_status"] == "EXPLICIT_NOT_CALENDAR_VERIFIED"


def test_unknown_derivative_does_not_inherit_nifty_calendar():
    with pytest.raises(ValueError, match="CALENDAR_UNSUPPORTED_INSTRUMENT"):
        resolve("AUTO", "DHAN", "UNKNOWN", AT, ProviderCalendars((synthetic_evidence(),)))
