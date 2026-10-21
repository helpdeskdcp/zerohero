"""Manual/browser evidence ingestion uses temporary files and synthetic CSVs."""
from datetime import date
import hashlib
import json
from pathlib import Path

import pytest

from calendar_evidence_acquisition.evidence_engine import (
    EvidenceEngine, identify_filename, select_link, BrowserUnavailable, guarded_url, playwright_download, main,
)

NAME = "BhavCopy_NSE_FO_0_0_0_20260903_F_0000.csv"
URL = "https://nsearchives.nseindia.com/reports/" + NAME
RAW = (b"TradDt,Src,Sgmt,TckrSymb,FinInstrmTp,XpryDt,FininstrmActlXpryDt,FinInstrmId,StrkPric,OptnTp\n"
       b"2026-09-03,NSE,FO,NIFTY,IDO,2026-09-08,2026-09-08,123,25000,CE\n")


def staged(tmp_path, raw=RAW, name=NAME, **overrides):
    engine = EvidenceEngine(tmp_path / "artifacts")
    engine.staging.mkdir(parents=True)
    file = engine.staging / name
    file.write_bytes(raw)
    meta = dict(exchange="NSE", segment="FO", trading_date="2026-09-03",
                provenance_url=URL, report_name="Synthetic fixture, not exchange evidence")
    meta.update(overrides)
    file.with_name(file.name + ".json").write_text(json.dumps(meta))
    return engine, file


def test_filename_detection_never_creates_a_download_url():
    assert identify_filename(NAME) == ("NSE", date(2026, 9, 3))
    assert identify_filename("fo05JUL2024bhav.csv.zip") == ("NSE", date(2024, 7, 5))
    with pytest.raises(ValueError, match="EXPLICIT_DATE_REQUIRED"):
        identify_filename("df030926.zip")
    assert identify_filename("df030926.zip", date(2026, 9, 3)) == ("BSE", date(2026, 9, 3))
    with pytest.raises(ValueError, match="FILENAME_DATE_MISMATCH"):
        identify_filename(NAME, date(2026, 9, 4))


def test_manual_ingestion_retains_hash_provenance_and_unverified_status(tmp_path):
    engine, file = staged(tmp_path, status="VERIFIED")  # Metadata cannot self-verify.
    entry = engine.ingest(file)
    assert entry["sha256"] == hashlib.sha256(RAW).hexdigest()
    assert entry["extracted_expiries"] == ["08-SEP-2026"]
    assert entry["provenance_url"] == URL
    assert entry["status"] == "UNVERIFIED"
    assert entry["publisher_status"] == "OPERATOR_ASSERTED"
    assert entry["provider_listing_status"] == "UNVERIFIED"
    assert entry["calendar_activation"] is False
    assert file.read_bytes() == RAW
    ledger = json.loads(engine.ledger.read_text())
    assert ledger["entries"] == [entry]


def test_identical_manual_source_is_idempotent(tmp_path):
    engine, file = staged(tmp_path)
    first = engine.ingest(file)
    ledger_bytes = engine.ledger.read_bytes()
    assert engine.ingest(file) == first
    assert engine.ledger.read_bytes() == ledger_bytes


def test_hash_mismatch_and_wrong_trade_date_do_not_create_verified_data(tmp_path):
    engine, file = staged(tmp_path, sha256="0" * 64)
    with pytest.raises(ValueError, match="SOURCE_HASH_MISMATCH"):
        engine.ingest(file)
    assert not engine.ledger.exists()


def test_unreadable_archive_is_retained_with_reason(tmp_path):
    engine, file = staged(tmp_path, b"PKbroken", NAME + ".zip")
    entry = engine.ingest(file)
    assert entry["status"] == "UNVERIFIED" and entry["reason"] == "PARSER_REJECTED"
    assert entry["extracted_expiries"] == []
    assert (engine.root / entry["raw_path"]).read_bytes() == b"PKbroken"


def test_metadata_date_disagreement_is_rejected(tmp_path):
    engine, file = staged(tmp_path, trading_date="2026-09-04")
    with pytest.raises(ValueError, match="FILENAME_DATE_MISMATCH"):
        engine.ingest(file)


def test_missing_source_and_external_staging_path_are_rejected(tmp_path):
    engine, file = staged(tmp_path, provenance_url="")
    with pytest.raises(ValueError):
        engine.ingest(file)
    elsewhere = tmp_path / NAME
    elsewhere.write_bytes(RAW)
    with pytest.raises(ValueError, match="STAGING_PATH_REQUIRED"):
        engine.ingest(elsewhere)


def test_source_collision_preserves_both_archives_and_flags_conflict(tmp_path):
    engine, file = staged(tmp_path)
    first = engine.ingest(file)
    file.write_bytes(RAW.replace(b"2026-09-08", b"2026-09-15"))
    second = engine.ingest(file)
    entries = json.loads(engine.ledger.read_text())["entries"]
    assert len(entries) == 2
    assert entries[0] == first
    assert second["status"] == "CONFLICTING" and second["conflicts"]
    assert (engine.root / first["raw_path"]).read_bytes() == RAW


def test_browser_selects_only_links_actually_present_for_requested_date():
    assert select_link([URL], "NSE", date(2026, 9, 3)) == URL
    with pytest.raises(BrowserUnavailable, match="NO_UNIQUE_REPORT_LINK"):
        select_link([URL], "NSE", date(2026, 9, 4))
    with pytest.raises(ValueError):
        guarded_url("https://api.dhan.co/v2/optionchain", "NSE")
    with pytest.raises(ValueError):
        guarded_url(URL + "?access_token=secret", "NSE")


def test_browser_failure_is_recorded_without_retry_or_ledger_activation(tmp_path):
    engine = EvidenceEngine(tmp_path / "artifacts")
    calls = []
    def browser(**kwargs):
        calls.append(kwargs)
        raise BrowserUnavailable("BROWSER_TIMEOUT")
    result = engine.browser("NSE", date(2026, 9, 3), "https://www.nseindia.com/all-reports-derivatives", adapter=browser)
    assert result["status"] == "MANUAL_REQUIRED"
    assert engine.browser("NSE", date(2026, 9, 3), "https://www.nseindia.com/all-reports-derivatives", adapter=browser) == result
    assert len(calls) == 1 and not engine.ledger.exists()


def test_browser_bytes_follow_same_ingestion_gate(tmp_path):
    engine = EvidenceEngine(tmp_path / "artifacts")
    def browser(**kwargs):
        return dict(raw=RAW, filename=NAME, source_url=URL, http_status=200,
                    discovery_url=kwargs["report_page"], discovery_body=b"<a href='" + URL.encode() + b"'>file</a>")
    result = engine.browser("NSE", date(2026, 9, 3), "https://www.nseindia.com/all-reports-derivatives", adapter=browser)
    assert result["entry"]["sha256"] == hashlib.sha256(RAW).hexdigest()
    assert result["entry"]["status"] == "UNVERIFIED"
    assert result["entry"]["acquisition_route"] == "BROWSER"
    (engine.root / result["entry"]["raw_path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="RETAINED_SOURCE_HASH_MISMATCH"):
        engine.browser("NSE", date(2026, 9, 3), "https://www.nseindia.com/all-reports-derivatives",
                       adapter=lambda **kwargs: pytest.fail("retry"))


def test_tampered_retained_archive_blocks_reuse(tmp_path):
    engine, file = staged(tmp_path)
    entry = engine.ingest(file)
    (engine.root / entry["raw_path"]).write_bytes(b"altered")
    with pytest.raises(ValueError, match="RETAINED_SOURCE_HASH_MISMATCH"):
        engine.ingest(file)


def test_atomic_ledger_failure_preserves_previous_entries(tmp_path, monkeypatch):
    engine, file = staged(tmp_path)
    engine.ingest(file)
    before = engine.ledger.read_bytes()
    file.write_bytes(RAW.replace(b"2026-09-08", b"2026-09-15"))
    def fail(*args):
        raise OSError("simulated filesystem failure")
    monkeypatch.setattr("calendar_evidence_acquisition.evidence_engine.os.replace", fail)
    with pytest.raises(OSError):
        engine.ingest(file)
    assert engine.ledger.read_bytes() == before


def test_tampered_inventory_blocks_idempotent_reuse(tmp_path):
    engine, file = staged(tmp_path)
    entry = engine.ingest(file)
    (engine.root / entry["inventory_path"]).write_text("{}")
    with pytest.raises(ValueError, match="RETAINED_INVENTORY_HASH_MISMATCH"):
        engine.ingest(file)


def test_missing_playwright_is_a_clear_manual_fallback(monkeypatch):
    import builtins
    real_import = builtins.__import__
    def blocked(name, *args, **kwargs):
        if name.startswith("playwright"):
            raise ImportError("unavailable in test")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(BrowserUnavailable, match="PLAYWRIGHT_NOT_INSTALLED"):
        playwright_download(exchange="NSE", trading_date=date(2026, 9, 3),
                            report_page="https://www.nseindia.com/all-reports-derivatives")


def test_manual_cli_never_claims_verified(tmp_path, capsys):
    engine, file = staged(tmp_path)
    assert main(["--artifacts", str(engine.root), "ingest", str(file)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "UNVERIFIED"


def test_playwright_timeout_becomes_manual_fallback(monkeypatch):
    import sys
    from types import ModuleType
    api = ModuleType("playwright.async_api")
    class FakeTimeout(Exception):
        pass
    class FakeError(Exception):
        pass
    class Session:
        async def __aenter__(self):
            raise FakeTimeout("simulated timeout")
        async def __aexit__(self, *args):
            pass
    api.async_playwright = Session
    api.TimeoutError = FakeTimeout
    api.Error = FakeError
    monkeypatch.setitem(sys.modules, "playwright", ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", api)
    with pytest.raises(BrowserUnavailable, match="BROWSER_TIMEOUT"):
        playwright_download(exchange="NSE", trading_date=date(2026, 9, 3),
                            report_page="https://www.nseindia.com/all-reports-derivatives")


@pytest.mark.parametrize("exchange,symbol", [("BSE", "SENSEX"), ("MCX", "NATURALGAS")])
def test_manual_explicit_field_map_keeps_expiry_and_maturity_separate(tmp_path, exchange, symbol):
    raw = ("Date,Symbol,Instrument,Expiry Date,Contract Maturity Date,ID\n"
           f"2026-09-03,{symbol},OPTION,2026-09-10,2026-09-24,123\n").encode()
    mapping = dict(version="synthetic-mapping/v1", trading_date="Date", underlying="Symbol",
                   instrument="Instrument", expiry="Expiry Date", maturity="Contract Maturity Date", identifier="ID")
    engine, file = staged(tmp_path, raw, "manual.csv", exchange=exchange,
                          segment="FO" if exchange == "BSE" else "COM",
                          provenance_url=f"https://www.{exchange.lower()}india.com/reports/manual.csv", field_map=mapping)
    if exchange == "MCX":
        with pytest.raises(ValueError, match="CALENDAR_EXCHANGE_DISABLED"):
            engine.ingest(file)
        assert not engine.ledger.exists()
        return
    entry = engine.ingest(file)
    inv = json.loads((engine.root / entry["inventory_path"]).read_text())
    assert inv["selected_observations"][0]["contract_maturity_date"] == "2026-09-24"
    assert entry["expiry_dates_iso"] == ["2026-09-10"]
    assert entry["status"] == "UNVERIFIED"


def test_file_level_expiry_union_retains_underlying_and_instrument_groups(tmp_path):
    extra = (b"2026-09-03,NSE,FO,BANKNIFTY,IDF,2026-09-29,2026-09-29,124,0,\n")
    engine, file = staged(tmp_path, RAW + extra)
    entry = engine.ingest(file)
    assert entry["expiry_observations"] == [
        dict(underlying="BANKNIFTY", instrument="IDF", expiry_dates_iso=["2026-09-29"]),
        dict(underlying="NIFTY", instrument="IDO", expiry_dates_iso=["2026-09-08"]),
    ]
