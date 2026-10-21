"""Synthetic exchange-format fixtures, never production-verified calendars."""
from dataclasses import replace
from datetime import date
import hashlib

import pytest

from calendar_evidence_acquisition.official_eod import (
    Discovery, FieldMap, EODPipeline, NSE_UDIFF, NSE_LEGACY,
    discover_reports, parse_eod, filter_observations, reconcile, required_windows, fetch_official,
)


URL = "https://www.bseindia.com/download/BhavCopy/Derivative/retained.csv"
PAGE = "https://www.bseindia.com/markets/marketinfo/bhavcopy"
BODY = ('<a href="' + URL + '">Equity Derivatives Bhavcopy</a>').encode()
BSE = FieldMap("bse-explicit-columns-fixture/v1", "Trading Date", "Underlying", "Instrument",
               "Expiry Date", maturity="Contract Maturity Date", identifier="Contract ID")
CSV = (b"Trading Date,Underlying,Instrument,Expiry Date,Contract Maturity Date,Contract ID\n"
       b"2026-09-03,SENSEX,OPTIDX,2026-09-10,2026-09-24,123\n")


def discovery(body=BODY, url=PAGE):
    return Discovery(url, 200, body, hashlib.sha256(body).hexdigest(), "2026-10-02T10:00:00Z")


def report():
    return discover_reports(discovery(), "BSE", "FO", date(2026, 9, 3),
                            "Equity Derivatives Bhavcopy")[0]


def test_only_links_in_successful_official_response_authorize_download():
    item = report()
    assert item.source_url == URL
    assert item.original_filename == "retained.csv"
    with pytest.raises(ValueError, match="DISCOVERY_HASH_MISMATCH"):
        discover_reports(replace(discovery(), sha256="0" * 64), "BSE", "FO", date(2026, 9, 3), "Bhavcopy")
    with pytest.raises(ValueError, match="DISCOVERY_UNAVAILABLE"):
        discover_reports(replace(discovery(), http_status=403), "BSE", "FO", date(2026, 9, 3), "Bhavcopy")
    assert discover_reports(discovery(b"<html>site shell</html>"), "BSE", "FO", date(2026, 9, 3), "Bhavcopy") == []


def test_secondary_mirror_cannot_authorize_official_download():
    with pytest.raises(ValueError, match="OFFICIAL_ORIGIN_REQUIRED"):
        discover_reports(discovery(url="https://github.com/example/mirror"), "BSE", "FO", date(2026, 9, 3), "Bhavcopy")
    other = b'<a href="https://example.org/data.csv">Bhavcopy</a>'
    assert discover_reports(discovery(other), "BSE", "FO", date(2026, 9, 3), "Bhavcopy") == []


def test_bse_official_json_listing_needs_exact_url_not_bare_filename():
    page = "https://api.bseindia.com/BseIndiaAPI/api/GetBhavcopydata/w"
    body = ('{"download":"' + URL + '","filename":"guessed.csv"}').encode()
    items = discover_reports(discovery(body, page), "BSE", "FO", date(2026, 9, 3), "Bhavcopy")
    assert [r.source_url for r in items] == [URL]


def test_arbitrary_url_cannot_be_substituted_for_discovered_link(tmp_path):
    forged = replace(report(), source_url="https://www.bseindia.com/guessed.csv")
    with pytest.raises(ValueError, match="REPORT_NOT_DISCOVERED"):
        EODPipeline(tmp_path).acquire(forged, BSE, lambda _: pytest.fail("network"))


def test_bse_expiry_is_not_replaced_by_maturity():
    inv = parse_eod(CSV, report(), BSE)
    row = inv["observations"][0]
    assert row["expiry_date"] == "2026-09-10"
    assert row["contract_maturity_date"] == "2026-09-24"
    assert row["contract_identifier"] == "123"
    assert row["evidence_classification"] == "OFFICIAL_EXCHANGE_EOD"
    assert inv["verification_status"] == "UNVERIFIED"
    assert inv["provider_listing_status"] == "UNVERIFIED"


def test_maturity_without_expiry_cannot_create_an_expiry():
    with pytest.raises(ValueError, match="REQUIRED_FIELD_MISSING"):
        parse_eod(CSV.replace(b"Expiry Date,", b"Other Date,"), report(), BSE)


def test_mcx_uses_declared_expiry_field_and_preserves_product_id():
    url = "https://www.mcxindia.com/reports/eod.csv"
    body = f'<a href="{url}">MCX Bhavcopy</a>'.encode()
    item = discover_reports(discovery(body, "https://www.mcxindia.com/market-data/bhavcopy"),
                            "MCX", "COM", date(2026, 9, 3), "MCX Bhavcopy")[0]
    fields = FieldMap("mcx-explicit-columns-fixture/v1", "Date", "Symbol", "Instrument", "Expiry Date", identifier="Product ID")
    raw = b"Date,Symbol,Instrument,Expiry Date,Product ID\n2026-09-03,NATURALGAS,OPTFUT,2026-09-24,NG-OPTION\n"
    row = parse_eod(raw, item, fields)["observations"][0]
    assert row["expiry_date"] == "2026-09-24" and row["contract_identifier"] == "NG-OPTION"
    assert row["contract_maturity_date"] is None


def test_checksum_duplicate_download_and_complete_metadata(tmp_path):
    calls = []
    def fetch(url):
        calls.append(url)
        return 200, CSV, ""
    pipeline = EODPipeline(tmp_path)
    first = pipeline.acquire(report(), BSE, fetch)
    assert first == pipeline.acquire(report(), BSE, fetch)
    assert calls == [URL]
    meta = first["source"]
    assert meta["sha256"] == hashlib.sha256(CSV).hexdigest()
    assert meta["parser_version"] and meta["field_map_version"]
    assert meta["original_filename"] == "retained.csv"
    assert meta["trading_date"] == "2026-09-03"
    assert meta["source_endpoint"] == PAGE
    assert meta["report_name"] and meta["acquired_at"] and meta["bytes"] == len(CSV)
    path = tmp_path / meta["cache_key"] / "raw.bin"
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="CACHE_HASH_MISMATCH"):
        pipeline.acquire(report(), BSE, fetch)
    assert len(calls) == 1


def test_filtering_uses_exchange_underlying_and_instrument_together():
    inv = parse_eod(CSV, report(), BSE)
    assert len(filter_observations(inv, "BSE", {"SENSEX"}, "OPTIDX")) == 1
    assert filter_observations(inv, "MCX", {"SENSEX"}, "OPTIDX") == []
    assert filter_observations(inv, "BSE", {"BANKEX"}, "OPTIDX") == []
    assert filter_observations(inv, "BSE", {"SENSEX"}, "FUTIDX") == []


def test_duplicates_and_same_contract_conflicts_preserve_observations():
    duplicate = CSV + CSV.splitlines(keepends=True)[1]
    inv = parse_eod(duplicate, report(), BSE)
    assert len(inv["observations"]) == 2 and inv["duplicate_rows"] == [3]
    changed = parse_eod(CSV.replace(b"2026-09-10", b"2026-09-17"), report(), BSE)
    result = reconcile([inv, changed])
    assert result["verification_status"] == "CONFLICTING"
    assert len(result["conflicts"]) == 1


def nse_report(day):
    url = "https://nsearchives.nseindia.com/reports/published.csv"
    body = f'<a href="{url}">F&amp;O Bhavcopy Final</a>'.encode()
    return discover_reports(discovery(body, "https://www.nseindia.com/all-reports-derivatives"),
                            "NSE", "FO", day, "F&O Bhavcopy Final")[0]


@pytest.mark.parametrize("day,fields,ok", [("2024-07-05", NSE_LEGACY, True), ("2024-07-08", NSE_LEGACY, False),
                                          ("2024-07-05", NSE_UDIFF, False), ("2024-07-08", NSE_UDIFF, True)])
def test_nse_july_2024_transition_is_explicit(day, fields, ok):
    raw = ((f"TIMESTAMP,SYMBOL,INSTRUMENT,EXPIRY_DT,STRIKE_PR,OPTION_TYP\n{day},NIFTY,OPTIDX,2024-07-25,25000,CE\n")
           if fields == NSE_LEGACY else
           (f"TradDt,Src,Sgmt,TckrSymb,FinInstrmTp,XpryDt,FininstrmActlXpryDt,FinInstrmId,StrkPric,OptnTp\n{day},NSE,FO,NIFTY,IDO,2024-07-25,2024-07-25,123,25000,CE\n")).encode()
    if ok:
        assert parse_eod(raw, nse_report(date.fromisoformat(day)), fields)["observations"][0]["expiry_date"] == "2024-07-25"
    else:
        with pytest.raises(ValueError, match="NSE_FORMAT_PERIOD_MISMATCH"):
            parse_eod(raw, nse_report(date.fromisoformat(day)), fields)


def test_eod_trading_date_must_match_discovered_report_request():
    with pytest.raises(ValueError, match="TRADING_DATE_MISMATCH"):
        parse_eod(CSV.replace(b"2026-09-03", b"2026-09-04"), report(), BSE)


def test_failure_is_cached_without_retries_or_calendar_claim(tmp_path):
    calls = []
    def fetch(url):
        calls.append(url)
        return 403, b"denied", ""
    pipeline = EODPipeline(tmp_path)
    result = pipeline.acquire(report(), BSE, fetch)
    assert result["verification_status"] == "UNVERIFIED" and result["observations"] == []
    assert pipeline.acquire(report(), BSE, fetch) == result
    assert calls == [URL]


def test_required_windows_do_not_assume_trading_days_or_create_urls():
    windows = required_windows()
    assert windows["NIFTY"] == ("NSE", "2026-07-01", "2026-10-01")
    assert not any(ex == "MCX" for ex, _, _ in windows.values())
    assert len(windows) == 6


def test_transport_rejects_broker_or_nonofficial_hosts_before_network():
    for url in ("https://api.dhan.co/v2/optionchain", "https://example.org/data.csv",
                "https://user:password@www.bseindia.com/data.csv"):
        with pytest.raises(ValueError):
            fetch_official(url)


def test_missing_expiry_retains_failed_parse_and_raw_bytes(tmp_path):
    raw = CSV.replace(b"2026-09-10", b"")
    pipeline = EODPipeline(tmp_path)
    result = pipeline.acquire(report(), BSE, lambda _: (200, raw, ""))
    assert result["reason"] == "PARSER_REJECTED"
    assert result["error"] == "INVALID_ARCHIVE_DATE"
    assert result["observations"] == [] and result["verification_status"] == "UNVERIFIED"
    assert (tmp_path / result["source"]["cache_key"] / "raw.bin").read_bytes() == raw


@pytest.mark.parametrize("raw", [b"PKbroken", b"\x1f\x8bbroken"])
def test_corrupt_archive_is_retained_and_reported_unavailable(tmp_path, raw):
    result = EODPipeline(tmp_path).acquire(report(), BSE, lambda _: (200, raw, ""))
    assert result["reason"] == "PARSER_REJECTED"
    assert result["verification_status"] == "UNVERIFIED" and result["observations"] == []
    assert (tmp_path / result["source"]["cache_key"] / "raw.bin").read_bytes() == raw


def test_nse_udiff_actual_expiry_is_preserved_without_overwriting_expiry():
    raw = (b"TradDt,Src,Sgmt,TckrSymb,FinInstrmTp,XpryDt,FininstrmActlXpryDt,FinInstrmId,StrkPric,OptnTp\n"
           b"2026-09-03,NSE,FO,NIFTY,IDO,2026-09-08,2026-09-07,123,25000,CE\n")
    row = parse_eod(raw, nse_report(date(2026, 9, 3)), NSE_UDIFF)["observations"][0]
    assert row["expiry_date"] == "2026-09-08"
    assert row["actual_expiry_date"] == "2026-09-07"
    assert row["contract_maturity_date"] is None
