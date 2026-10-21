"""Retained first-party links are acquisition candidates, never expiry evidence."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit
import pytest

ROOT=Path(__file__).resolve().parents[2]
PLAN=ROOT/'backend/calendar_evidence_acquisition/parsed/own_exchange_source_fallbacks_20261004.json'


def plan():
    return json.loads(PLAN.read_text())


def test_ordered_fallbacks_do_not_promote_a_link_to_an_expiry():
    p=plan()
    assert [r['priority'] for r in p['bse_sources']]==[1,1,2,3,4,5]
    assert [r['priority'] for r in p['mcx_sources']]==[1,2,3,4,5]
    for row in p['bse_sources']+p['mcx_sources']:
        assert row['evidence_classification']=='MISSING_EVIDENCE'
        assert row['observed_expiries']==[]
    assert p['production_calendar']=='NOT_INSTALLED' and p['auto_next']=='FAIL-CLOSED'


def test_each_bse_link_is_literal_first_party_discovery_with_retained_hash():
    for row in plan()['bse_sources']:
        d=row['discovery'];raw=(ROOT/d['raw_path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==d['sha256']
        assert d['exact_reference'].encode() in raw
        assert raw[d['byte_offset']:].startswith((b'["href",',b'["routerLink",'))
        assert urljoin(d['source_url'],d['exact_reference'])==row['source_url']
        assert urlsplit(row['source_url']).hostname=='www.bseindia.com'
        assert row['artifact_sha256'] is None and row['raw_path'] is None


def test_official_ui_distinguishes_live_master_from_samples_and_currency():
    p=plan();master=p['bse_sources'][0];d=master['discovery']
    text=(ROOT/d['raw_path']).read_text()
    assert 'Production Environment Master Files' in text and 'Contract Master' in text
    assert master['source_url']=='https://www.bseindia.com/downloads1/Live_Masters_for_Equity_Derivatives.zip'
    assert p['bse_sources'][1]['role']=='OFFICIAL_SCHEMA_AND_SAMPLE_CANDIDATE'
    assert {r['reason'] for r in p['bse_excluded_candidates']}=={'TEST_DATA_NOT_PRODUCTION_EVIDENCE','OFFICIAL_UI_ASSOCIATES_WITH_CURRENCY_DERIVATIVES_NOT_SENSEX'}


def test_api_names_cannot_authorize_guessed_request_parameters():
    for row in plan()['bse_api_references']:
        d=row['discovery'];raw=(ROOT/d['raw_path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==d['sha256']
        assert row['endpoint_reference'].encode() in raw
        assert row['request_url'] is None and row['request_parameters'] is None
        assert row['status']=='REQUEST_CONTRACT_UNCONFIRMED'


def test_denial_retained_without_claiming_exchange_data_does_not_exist():
    p=plan();r=p['new_network_observation'];raw=(ROOT/r['raw_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==r['sha256']
    assert r['source_url']=='https://api.bseindia.com/robots.txt' and r['http_status']==403
    assert p['new_network_requests']==1 and p['retired_or_denied_endpoints_retried']==0
    assert p['exchange_has_no_official_data_claim'] is False


@pytest.mark.parametrize('symbol',['SENSEX','NATURALGAS','NATGASMINI','CRUDEOIL','CRUDEOILM'])
def test_each_instrument_remains_missing_until_records_and_lifecycle_are_acquired(symbol):
    row=next(r for r in plan()['instruments'] if r['instrument']==symbol)
    assert row['observed_official_expiries']==[] and row['validated_production_expiries']==[]
    assert row['inferred_derived_expiries']==[] and row['classification']=='MISSING_EVIDENCE'
    assert row['complete_contract_master'] is False and row['production_ready'] is False
