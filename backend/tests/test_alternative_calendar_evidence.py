"""Retained observations must never become invented historical calendar facts."""
import collections
import csv
import gzip
import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "backend/calendar_evidence_acquisition"


def read(name):
    return json.loads((BASE / name).read_text())


@pytest.fixture(scope="module")
def master():
    inventory = read("parsed/dhan_master_contract_inventory.json")
    with (ROOT / inventory["source"]["path"]).open() as handle:
        rows = list(csv.DictReader(handle))
    return inventory, rows


def test_master_projection_preserves_actual_dates_types_sides_and_flags(master):
    """No generated expiry or inferred weekly/monthly flag enters the inventory."""
    inventory, rows = master
    fields = inventory["grouping_fields"]
    target = inventory["instrument_symbols_as_stored"]
    actual = collections.Counter(
        tuple(row[field] for field in fields) for row in rows
        if row["UNDERLYING_SYMBOL"] in target.get(row["EXCH_ID"], [])
        and row["INSTRUMENT"] in {"OPTIDX", "FUTIDX", "OPTFUT", "FUTCOM"}
    )
    retained = {tuple(row[field] for field in fields): row["row_count"]
                for row in inventory["groups"]}
    assert actual == retained
    assert inventory["total_master_rows"] == len(rows)
    assert inventory["selected_rows"] == sum(actual.values())


def test_same_symbol_other_exchange_is_not_merged_into_mcx(master):
    """NSE commodity options cannot corroborate an MCX contract identity."""
    inventory, rows = master
    assert any(r["EXCH_ID"] == "NSE" and r["UNDERLYING_SYMBOL"] == "CRUDEOIL"
               and r["INSTRUMENT"] == "OPTFUT" for r in rows)
    selected = [r for r in inventory["groups"] if r["UNDERLYING_SYMBOL"] == "CRUDEOIL"]
    assert selected and {r["EXCH_ID"] for r in selected} == {"MCX"}
    assert any(r["exchange"] == "NSE" and r["underlying"] == "CRUDEOIL"
               for r in inventory["excluded_other_exchange_groups"])


def test_local_expiry_labels_retain_third_provider_derivation():
    """A Dhan price-source label does not authenticate an Upstox-derived expiry."""
    inventory = read("parsed/local_rolling_contract_inventory.json")
    raw = json.loads(gzip.decompress((ROOT / inventory["retained_projection"]["path"]).read_bytes()))
    assert len(raw["records"]) == inventory["row_count"]
    assert len(raw["expiry_audit"]) == inventory["audit_rows"]
    assert {r["source"] for r in raw["records"]} == {"DHAN_ROLLING"}
    assert all("Upstox" in r["source_reference"] for r in raw["expiry_audit"])
    actual = sorted({r["expiry"] for r in raw["records"]})
    assert inventory["distinct_expiry_labels"] == actual
    assert inventory["historical_calendar_coverage_start"] is None
    assert inventory["historical_calendar_coverage_end"] is None


def test_new_acquisitions_have_traceable_bytes_and_no_duplicate_requests():
    """Retained hashes authenticate acquired bytes, not historical applicability."""
    summary = read("parsed/alternative_acquisition_summary.json")
    register = read("source_register.json")["sources"]
    urls = [r["url"] for r in register]
    assert len(urls) == len(set(urls))
    sources = [r for r in register if r["source_id"] in summary["new_source_ids"]]
    assert len(sources) == summary["network"]["unique_sources_attempted"]
    for source in sources:
        assert source["retrieved_at"] and source["source_family"]
        assert source["what_it_proves"] and source["what_it_does_not_prove"]
        assert source["retry_attempted"] is False
        assert source["historical_calendar_usable"] is False
        if source["path"]:
            payload = (ROOT / source["path"]).read_bytes()
            assert hashlib.sha256(payload).hexdigest() == source["sha256"]
            assert len(payload) == source["bytes"]
        else:
            assert source["sha256"] is None and source["error"]
    for name, field in [("parsed/dhan_master_contract_inventory.json", "source"),
                        ("parsed/local_rolling_contract_inventory.json", "retained_projection")]:
        reference = read(name)[field]
        assert hashlib.sha256((ROOT / reference["path"]).read_bytes()).hexdigest() == reference["sha256"]


def test_transport_failure_is_not_relabelled_as_missing_historical_data():
    """Failed transport provides no evidence about the historical universe."""
    source = next(r for r in read("source_register.json")["sources"]
                  if r["source_id"] == "nse_historical_contract_report")
    assert source["curl_exit"] == 92
    assert source["http_status"] == "000"
    assert source["classification"] == "ACCESS_BLOCKED"
    assert source["coverage_start"] is None and source["coverage_end"] is None
    assert source["path"] is None


def test_current_observations_cannot_activate_historical_calendars(master):
    """Every unresolved provider identity keeps a separate acquisition gate."""
    inventory, _ = master
    assert inventory["observed_expiry_min"] > inventory["retrieved_at"][:10]
    assert inventory["historical_coverage_start"] is None
    assert inventory["historical_coverage_end"] is None
    manifest = read("manifest.json")
    assert len(manifest["matrix"]) == 28
    assert manifest["production_calendar_installed"] is False
    assert manifest["production_calendar_versions"] == []
    for row in manifest["matrix"]:
        plan = row["alternative_acquisition_plan"]
        assert row["status"] == plan["calendar_status"] == "UNVERIFIED"
        assert plan["observed_historical_calendar_coverage"] is None
        assert row["provider"] in plan["best_provider_source"]
        assert plan["exchange_evidence_missing"] and plan["provider_evidence_missing"]
