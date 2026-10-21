"""Synthetic format fixtures test tooling, never production calendar evidence."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import zipfile
from datetime import date, datetime, timezone

import pytest

from calendar_evidence_acquisition.static_archives import (
    SourceCache, SourceSpec, parse_archive, observed_expiries, holiday_candidate,
)
from app.hist_options.provider_calendar import ProviderCalendars, resolve


LEGACY = b"INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,TIMESTAMP\nOPTIDX,NIFTY,18-Aug-2026,25000,CE,13-Aug-2026\n"
UDIFF = b"TradDt,Src,FinInstrmTp,TckrSymb,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp\n2026-08-13,NSE,IDO,NIFTY,2026-08-18,2026-08-18,25000,PE\n"


def spec(source_class="OFFICIAL_ARCHIVE", url="https://archives.nseindia.com/fixture.csv"):
    return SourceSpec(url, source_class, "NSE", "FO_BHAVCOPY")


def zip_bytes(payload):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("fixture.csv", payload)
    return out.getvalue()


@pytest.mark.parametrize("payload", [LEGACY, gzip.compress(LEGACY), zip_bytes(LEGACY), zip_bytes(UDIFF)])
def test_official_formats_extract_observed_dates_only(payload):
    result = parse_archive(payload, spec())
    assert len(result["observations"]) == 1
    obs = result["observations"][0]
    assert (obs["observed_on"], obs["expiry"], obs["underlying"]) == ("2026-08-13", "2026-08-18", "NIFTY")
    assert result["verification_status"] == "UNVERIFIED"
    assert result["provider"] is None


def test_master_requires_explicit_archive_date_not_acquisition_date():
    data = b"Instrument,Symbol,ExpiryDate,StrikePrice,OptionType\nOPTIDX,NIFTY,18-Aug-2026,25000,CE\n"
    with pytest.raises(ValueError, match="OBSERVATION_DATE_REQUIRED"):
        parse_archive(data, spec())
    result = parse_archive(data, spec(), archive_date=date(2026, 8, 13))
    assert result["observations"][0]["observed_on"] == "2026-08-13"
    assert result["date_basis"] == "CALLER_DECLARED_ARCHIVE_DATE"


def test_cache_reuse_hash_and_duplicate_source_prevention(tmp_path):
    cache = SourceCache(tmp_path)
    calls = []
    def fetch(url):
        calls.append(url)
        return 200, LEGACY, ""
    first = cache.acquire(spec(), fetch)
    assert first == cache.acquire(spec(), fetch)
    assert calls == [spec().source_url]
    assert first["sha256"] == hashlib.sha256(LEGACY).hexdigest()
    (tmp_path / first["cache_key"] / "raw.bin").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="CACHE_HASH_MISMATCH"):
        cache.acquire(spec(), fetch)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [403, 404, 429, 503, 0])
def test_failure_is_retained_and_never_automatically_retried(tmp_path, status):
    cache = SourceCache(tmp_path)
    calls = []
    def fetch(url):
        calls.append(url)
        return status, b"denied", "transport" if status == 0 else ""
    result = cache.acquire(spec(), fetch)
    assert result["acquisition_status"] != "AVAILABLE"
    assert result["permanent_failure"] == (status in (403, 404))
    assert cache.acquire(spec(), fetch) == result
    assert len(calls) == 1


def test_existing_response_import_requires_hash_and_never_fetches(tmp_path):
    cache = SourceCache(tmp_path)
    with pytest.raises(ValueError, match="SOURCE_HASH_MISMATCH"):
        cache.retain(spec(), LEGACY, sha256="0" * 64)
    record = cache.retain(spec(), LEGACY, sha256=hashlib.sha256(LEGACY).hexdigest())
    assert cache.acquire(spec(), lambda _: pytest.fail("duplicate download")) == record
    with pytest.raises(ValueError, match="CACHE_IDENTITY_CONFLICT"):
        cache.acquire(SourceSpec(spec().source_url, "SECONDARY_DATASET", "BSE", "OTHER"), None)


def test_secondary_cannot_be_promoted_by_mirrored_official_csv():
    secondary = spec("SECONDARY_DATASET", "https://raw.githubusercontent.com/example/data/main/fo.csv")
    assert parse_archive(LEGACY, secondary)["source_class"] == "SECONDARY_DATASET"
    with pytest.raises(ValueError, match="OFFICIAL_ORIGIN_REQUIRED"):
        parse_archive(LEGACY, spec("OFFICIAL_ARCHIVE", secondary.source_url))


def test_offline_lookup_keeps_exchange_provider_and_date_separate():
    data = parse_archive(LEGACY, spec())
    assert observed_expiries([data], "NSE", "NIFTY", "OPTION", date(2026, 8, 13))["expiries"] == ["2026-08-18"]
    assert observed_expiries([data], "MCX", "NIFTY", "OPTION", date(2026, 8, 13))["status"] == "DATA_UNAVAILABLE"
    assert observed_expiries([data], "NSE", "NIFTY", "OPTION", date(2026, 8, 12))["status"] == "DATA_UNAVAILABLE"
    assert observed_expiries([data], "NSE", "NIFTY", "OPTION", date(2026, 8, 13), provider="DHAN")["reason"] == "PROVIDER_LISTING_NOT_ESTABLISHED"


def test_inconsistent_expiry_or_exchange_rejected():
    with pytest.raises(ValueError, match="EXPIRY_FIELDS_CONFLICT"):
        parse_archive(UDIFF.replace(b"2026-08-18,2026-08-18", b"2026-08-18,2026-08-17"), spec())
    with pytest.raises(ValueError, match="EXCHANGE_MISMATCH"):
        parse_archive(UDIFF.replace(b",NSE,", b",BSE,"), spec())


def test_missing_date_is_not_filled_and_bad_rows_are_not_silently_discarded():
    with pytest.raises(ValueError, match="INVALID_ARCHIVE_DATE"):
        parse_archive(LEGACY.replace(b"18-Aug-2026", b"bogus"), spec())
    with pytest.raises(ValueError, match="UNSUPPORTED_ARCHIVE_SCHEMA"):
        parse_archive(b"<html>access denied</html>", spec())


def test_secondary_mixed_date_encodings_are_quarantined_without_epoch_guessing():
    raw = b"Date,Expiry,Strike Price,Option Type\n02-Jan-2017,25-Jan-2017,8200,CE\n42857,42880,8200,CE\n"
    source = spec("SECONDARY_DATASET", "https://example.org/options.csv")
    result = parse_archive(raw, source, secondary_identity=("NIFTY", "OPTION"), quarantine_invalid=True)
    assert len(result["observations"]) == 1
    assert result["rejected_rows"] == [{"row_number": 3, "reason": "INVALID_ARCHIVE_DATE"}]
    assert result["continuous_coverage_proven"] is False


def test_holiday_adjustment_remains_derived_and_requires_complete_session_evidence():
    closed, opened = date(2026, 8, 18), date(2026, 8, 17)
    result = holiday_candidate(closed, {closed: False, opened: True},
                               effective_from=opened, effective_to=date(2026, 8, 19),
                               rule_reference="synthetic-rule-fixture", holiday_reference="synthetic-session-fixture")
    assert result["expiry"] == opened.isoformat()
    assert result["source_class"] == "DERIVED_RULE"
    assert result["verification_status"] == "UNVERIFIED"
    with pytest.raises(ValueError, match="SESSION_EVIDENCE_MISSING"):
        holiday_candidate(closed, {closed: False}, effective_from=opened,
                          effective_to=date(2026, 8, 19), rule_reference="fixture", holiday_reference="fixture")


def test_production_resolver_remains_offline_and_closed_without_provider_evidence():
    # The isolated runner blocks network, subprocesses, .env and production DBs.
    at = datetime(2026, 8, 13, 10, tzinfo=timezone.utc)
    for provider in ("DHAN", "ANGELONE"):
        with pytest.raises(ValueError, match="CALENDAR_EVIDENCE_UNAVAILABLE"):
            resolve("AUTO", provider, "NIFTY", at, ProviderCalendars())
        assert resolve("2026-08-18", provider, "NIFTY", at)[0] == date(2026, 8, 18)


@pytest.mark.parametrize("dataset_id", ["nifty_options_elt_bundle", "nifty_options_dataset_bundle"])
def test_retained_secondary_inventories_reproduce_from_hashed_bytes(dataset_id):
    """Real cached CSVs are reproducible; malformed date encodings stay rejected."""
    base = Path(__file__).resolve().parents[1] / "calendar_evidence_acquisition"
    summary = json.loads((base / "parsed/static_archive_observations.json").read_text())
    source = next(x for x in summary["datasets"] if x["source_id"] == dataset_id)
    path = base.parents[1] / source["parsed_inventory_path"]
    inv = json.loads(path.read_text())
    raw = (path.parent / "dataset.csv").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == inv["source_sha256"]
    actual = parse_archive(raw, SourceSpec(inv["source_url"], inv["source_class"], inv["exchange"], inv["dataset"]),
                           secondary_identity=("NIFTY", "OPTION") if inv["identity_basis"] == "DATASET_DESCRIPTION" else None,
                           quarantine_invalid=bool(inv["rejected_rows"]))
    assert actual["observations"] == inv["observations"]
    assert actual["rejected_rows"] == inv["rejected_rows"]
    assert inv["source_class"] == "SECONDARY_DATASET"
    assert inv["verification_status"] == "UNVERIFIED" and inv["provider"] is None


def test_incomplete_cache_attempt_is_not_automatically_retried(tmp_path):
    folder = tmp_path / hashlib.sha256(spec().source_url.encode()).hexdigest()
    folder.mkdir()
    with pytest.raises(ValueError, match="CACHE_INCOMPLETE_ATTEMPT"):
        SourceCache(tmp_path).acquire(spec(), lambda _: pytest.fail("retry"))


def test_out_of_period_rule_does_not_generate_a_candidate():
    with pytest.raises(ValueError, match="RULE_EVIDENCE_UNAVAILABLE"):
        holiday_candidate(date(2026, 8, 18), {date(2026, 8, 18): True},
                          effective_from=date(2026, 9, 1), effective_to=date(2026, 10, 1),
                          rule_reference="future-rule-fixture", holiday_reference="fixture")


def test_eod_observations_cannot_answer_intraday_timezone_queries():
    """A dated bhavcopy has no proven publication instant; reject intraday lookup."""
    with pytest.raises(ValueError, match="OBSERVATION_DATE_REQUIRED_NOT_ASOF"):
        observed_expiries([parse_archive(LEGACY, spec())], "NSE", "NIFTY", "OPTION",
                         datetime.fromisoformat("2026-08-13T00:01:00+05:30"))
