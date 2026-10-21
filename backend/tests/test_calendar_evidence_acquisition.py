"""Review-package integrity, not verification of actual exchange expiry dates."""
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.hist_options.provider_calendar import EXCHANGES, ProviderCalendars, resolve
from calendar_fixtures import synthetic_evidence

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "backend/validation/calendar_acquisition/manifest.json"


def package():
    return json.loads(PACKAGE.read_text())


def test_matrix_covers_each_registered_identity_and_provider_once():
    """No mini, registry-only metal, or provider can disappear from acquisition scope."""
    rows = package()["matrix"]
    expected = {(provider, venue, symbol) for symbol, venue in EXCHANGES.items()
                for provider in ("DHAN", "ANGELONE")}
    actual = [(r["provider"], r["exchange"], r["underlying"]) for r in rows]
    assert len(actual) == len(set(actual)) == 28
    assert set(actual) == expected


def test_unknown_ranges_dates_and_reviews_are_not_promoted_to_facts():
    """Unspecified owner scope and absent source facts stay explicitly unresolved."""
    p = package()
    assert p["package_schema"] == "calendar-evidence-acquisition/v1"
    assert p["package_version"]
    assert p["production_calendar_versions"] == []
    for row in p["matrix"]:
        assert row["status"] in p["status_definitions"]
        assert row["status"] == "UNVERIFIED"
        assert row["historical_date_range"]["state"] == "OWNER_RANGE_REQUIRED"
        assert row["historical_date_range"]["start"] is None
        assert row["historical_date_range"]["end_exclusive"] is None
        assert row["expiry_dates_required"]["exact_dates"] is None
        assert row["calendar_version"] is None
        assert row["calendar_evidence_sha256"] is None
        assert row["source_date"] is None
        assert row["independent_verification"]["verified"] is False


def test_acquisition_requests_retain_identity_scope_and_reason():
    """Shared sources never erase which provider/product needs which evidence."""
    p = package()
    requests = {r["id"]: r for r in p["acquisition_requests"]}
    assert len(requests) == len(p["acquisition_requests"])
    referenced = set()
    for row in p["matrix"]:
        types = set()
        for ref in row["acquisition_request_ids"]:
            r = requests[ref]
            referenced.add(ref)
            for key in ("provider", "exchange", "underlying", "instrument"):
                assert r[key] == row[key]
            assert r["date_range_required"] == row["historical_date_range"]
            assert r["why_required"] and r["source_required"]
            assert set(r["requirement_refs"]) <= p["requirements"].keys()
            types.add(r["request_type"])
        assert {"EXCHANGE_CONTRACTS", "EXCHANGE_SESSIONS", "LIFECYCLE_RULES",
                "PROVIDER_ARCHIVE", "INDEPENDENT_REVIEW"} <= types
        assert ("CONTRACT_MAPPING" in types) == (row["exchange"] == "MCX")
        assert set(row["source_refs"]) <= p["source_catalog"].keys()
    assert referenced == requests.keys()


def test_retained_source_hashes_and_document_dates_are_traceable():
    """Integrity can be checked independently; retrieval never invents publication."""
    for source in package()["source_catalog"].values():
        if "retained_path" in source:
            path = (ROOT / source["retained_path"]).resolve()
            assert path.is_relative_to(ROOT)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]
        if "retrieved_at" in source:
            assert datetime.fromisoformat(source["retrieved_at"]).tzinfo is not None
            assert source["publication_date"] is None
        assert source["usable_as_historical_calendar"] is False


def test_rule_candidates_cannot_be_mistaken_for_official_transition_dates():
    """Code boundaries and sentinel dates cannot become reviewed calendar periods."""
    p = package()
    for candidate in p["rule_change_candidates"]:
        assert candidate["source_reference"].startswith("backend/app/hist_options/expiry.py:")
        assert candidate["status"] == "UNVERIFIED"
        assert candidate["official_effective_from"] is None
        assert candidate["official_effective_to"] is None
        assert candidate["primary_document"] is None
    assert p["banknifty_weekly_guard"]["actual_discontinuation_date"] is None


@pytest.mark.parametrize("status", ["PARTIALLY_VERIFIED", "UNVERIFIED", "CONFLICTING", "NOT_APPLICABLE"])
def test_acquisition_statuses_cannot_authorize_auto_or_next(status):
    """Even complete test manifests need VERIFIED review, not acquisition progress."""
    evidence = replace(synthetic_evidence(), verification_status=status)
    for alias in ("AUTO", "NEXT"):
        with pytest.raises(ValueError, match="CALENDAR_NOT_VERIFIED"):
            resolve(alias, "DHAN", "NIFTY", datetime(2026, 9, 2, tzinfo=timezone.utc),
                    ProviderCalendars((evidence,)))
