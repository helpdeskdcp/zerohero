"""Evidence-plan integrity; these tests do not authenticate production calendars."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from app.hist_options.provider_calendar import EXCHANGES

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "backend/calendar_evidence_acquisition/manifest.json"


def plan():
    return json.loads(PLAN.read_text())


def test_all_provider_instrument_combinations_have_separate_plans():
    p = plan()
    identities = [(r["provider"], r["exchange"], r["underlying"]) for r in p["matrix"]]
    assert len(identities) == len(set(identities)) == 28
    assert set(identities) == {(provider, venue, symbol)
                               for symbol, venue in EXCHANGES.items()
                               for provider in ("DHAN", "ANGELONE")}
    for row in p["matrix"]:
        assert row["primary_source_required"]["provider"] == row["provider"] + "_HISTORICAL_ARCHIVE"


def test_required_ranges_are_derived_from_named_context_not_copied_from_spot():
    p = plan()
    for row in p["matrix"]:
        context = row["required_range_basis"]
        if context:
            assert all(x["basis"] and x["source"] and x["timestamp_semantics"] for x in context)
            assert row["earliest_required_date"] == min(x["earliest"] for x in context)
            assert row["latest_required_date"] == max(x["latest"] for x in context)
        else:
            assert row["earliest_required_date"] is None
            assert row["latest_required_date"] is None
            assert row["range_status"] == "NOT_ESTABLISHED"
            assert row["range_limitation"]
    assert p["proposed_coverage"]["start"] == min(
        r["earliest_required_date"] for r in p["matrix"] if r["earliest_required_date"])
    assert p["proposed_coverage"]["end_inclusive"] == max(
        r["latest_required_date"] for r in p["matrix"] if r["latest_required_date"])


def test_unproved_mini_and_metal_scope_is_not_inherited_from_main_products():
    unresolved = {"NATGASMINI", "CRUDEOILMINI", "GOLD", "GOLDMINI", "SILVER", "SILVERMINI"}
    actual = {r["underlying"] for r in plan()["matrix"] if r["earliest_required_date"] is None}
    assert actual == unresolved


def test_daily_bar_boundary_uses_ist_and_does_not_extend_to_expiry_date():
    p = plan()
    inventory = json.loads((ROOT / p["coverage_inventory"]["path"]).read_text())
    bars = next(x for x in inventory["database_ranges"] if x["table"] == "market_candles")
    row = next(r for r in bars["ranges"] if r["symbol"] == "NIFTY"
               and r["kind"] == "OPTION" and r["tf"] == "1d")
    observed_date = datetime.fromisoformat(row["earliest"]).astimezone(ZoneInfo("Asia/Kolkata")).date()
    assert observed_date.isoformat() == p["proposed_coverage"]["start"]
    assert row["earliest"][:10] != observed_date.isoformat()
    assert p["proposed_coverage"]["not_a_global_api_bound"] is True


def test_acquisition_plan_cannot_claim_a_verified_calendar():
    p = plan()
    assert p["package_schema"] == "calendar-evidence-acquisition/v2"
    assert p["production_calendar_versions"] == []
    assert p["production_calendar_installed"] is False
    assert p["final_calendar_evidence_hash"] is None
    assert p["final_calendar_version"] is None
    for row in p["matrix"]:
        assert row["status"] == "UNVERIFIED"
        assert row["final_evidence"] == dict(sha256=None, calendar_version=None,
                                              review_reference=None, status="UNVERIFIED")
        assert row["exact_evidence_required"] and row["verification_method"]


def test_source_references_classifications_and_inventory_hash_are_reviewable():
    p = plan()
    source = p["coverage_inventory"]
    assert hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest() == source["sha256"]
    for entry in p["sources"].values():
        assert entry["classification"] in p["source_classifications"]
    for row in p["matrix"]:
        for ref in row["primary_source_required"].values():
            assert row["current_access_status"][ref] == p["sources"][ref]["classification"]
        assert set(row["secondary_corroboration_source"]) <= p["sources"].keys()


def test_network_failures_are_not_relabelled_as_absent_historical_evidence():
    p = plan()
    nse = next(x for x in p["network_diagnostics"] if x["source"] == "NSE_CIRCULAR_INDEX")
    assert nse["tls_failure_observed"] is False
    assert nse["http_status_failure_observed"] is False
    assert any("DNS_FAILURE" in x for x in nse["observed_failures"])
    assert any("HTTP2_PROTOCOL_FAILURE" in x for x in nse["observed_failures"])
    mcx = next(x for x in p["network_diagnostics"] if x["source"] == "MCX_PUBLIC")
    assert "HTTP_STATUS_FAILURE:403" in mcx["observed_failures"]
    assert "ACCESS_DENIED" in mcx["observed_failures"]
    assert p["sources"]["DHAN_PUBLIC"]["classification"] == "INSUFFICIENT"
