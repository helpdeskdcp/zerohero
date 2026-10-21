"""Completion gates never turn observed subsets into production calendars."""
from copy import deepcopy
from pathlib import Path
from datetime import datetime, timezone
import json
import pytest

from app.nse_official.controls import compare_market_sources, production_gate, validation_status
from app.nse_official import NSEAdapter, NSEStore, SourceRegistry


@pytest.mark.parametrize('result,expected',[
    ({'status':'OK','dataset_type':'option_chain','freshness_status':'LIVE'},'VALID_LIVE'),
    ({'status':'OK','dataset_type':'option_chain','freshness_status':'RECENT'},'VALID_RECENT'),
    ({'status':'DATA_UNAVAILABLE','reason':'STALE_SOURCE'},'STALE'),
    ({'status':'DATA_UNAVAILABLE','reason':'INCOMPLETE_CHAIN_CONTRACT','freshness_status':'STALE'},'PLACEHOLDER'),
    ({'status':'SOURCE_METADATA_REQUIRED'},'SOURCE_METADATA_REQUIRED'),
    ({'status':'OK','dataset_type':'historical'},'VALID_HISTORICAL'),
])
def test_status_does_not_equate_http_success_and_live(result,expected):
    assert validation_status(result)==expected


def pair():
    a=dict(source='NSE_OFFICIAL',exchange='NSE',symbol='NIFTY',instrument_type='OPTIDX',expiry='2026-10-06',
           strike=22000,option_type='CE',timeframe='SNAPSHOT',source_timestamp='2026-10-03T04:00:00Z',
           data_timestamp='2026-10-03T04:00:00Z',received_timestamp='2026-10-03T04:00:01Z',
           quantity_unit='CONTRACTS',ltp=100,open=99,high=102,low=98,close=100,volume=100,oi=300,oi_change=20)
    return a,dict(a,source='DHAN')


@pytest.mark.parametrize('mutation,expected', [('equal','MATCH'),('small','MINOR_DISCREPANCY'),
    ('large','MAJOR_DISCREPANCY'),('expiry','MAPPING_MISMATCH'),('stale','STALE_SOURCE'),('missing','MAPPING_MISMATCH')])
def test_comparison_is_explicit_and_never_mutates(mutation,expected):
    a,b=pair()
    if mutation=='small':b['ltp']=100.1
    if mutation=='large':b['ltp']=103
    if mutation=='expiry':b['expiry']='2026-10-13'
    if mutation=='stale':b['source_timestamp']='2026-10-01T04:00:00Z'
    if mutation=='missing':b['oi']=None
    before=deepcopy((a,b))
    r=compare_market_sources(a,b,as_of='2026-10-03T04:00:02Z',minor_tolerances={'ltp':.5})
    assert r['STATUS']==expected and (a,b)==before
    if mutation=='small':assert r['fields']['ltp']['DIFFERENCE']==pytest.approx(-.1)


def test_gate_never_activates_from_pass_claims():
    r=production_gate({k:{'status':'PASS'} for k in ('full_suite','contract_master','expiry_calendar',
        'historical_ingestion','live_freshness','provenance','duplicates','discrepancy')})
    assert r['PRODUCTION']=='BLOCKED' and r['AUTO/NEXT']=='FAIL-CLOSED' and r['LIVE_ORDERS']=='DISABLED'
    assert not r['eligible_for_review']


def test_validation_receipt_retains_rejected_raw(tmp_path):
    from app.nse_official.provenance import Response
    root=Path(__file__).resolve().parents[2]
    registry=SourceRegistry.from_inventory(root/'backend/calendar_evidence_acquisition/parsed/nse_market_data_20261003.json',root)
    a=NSEAdapter(registry,NSEStore(tmp_path),clock=lambda:datetime(2026,10,3,6,tzinfo=timezone.utc),
                 transport=lambda obs:Response(b'bad json',200,'2026-10-03T05:59:00Z',obs.endpoint,'application/json'))
    r=a.fetch_nse_historical_contract_data(symbol='NIFTY',instrument_type='OPTIDX',trade_date='2026-10-01',expiry='2026-10-06',option_type='CE')
    receipts=list((tmp_path/'validations').glob('*.json'));assert len(receipts)==1
    saved=json.loads(receipts[0].read_text())
    assert saved['reason']=='INVALID_JSON' and saved['sha256']==r['sha256'] and saved['rows']==[]
    assert (tmp_path/'raw'/r['sha256']).read_bytes()==b'bad json'


def test_master_is_observed_subset_not_selector_cross_product(tmp_path):
    from app.nse_official.master import build_preview
    root=Path(__file__).resolve().parents[2]
    registry=SourceRegistry.from_inventory(root/'backend/calendar_evidence_acquisition/parsed/nse_market_data_20261003.json',root)
    preview=build_preview(NSEAdapter(registry,NSEStore(tmp_path),clock=lambda:datetime(2026,10,3,6,tzinfo=timezone.utc)))
    assert len(preview['contracts'])==70 and len(preview['expiry_preview'])==18
    assert not preview['contract_universe_complete'] and preview['production_calendar']=='NOT_INSTALLED'
    assert {r['symbol'] for r in preview['contracts']}=={'NIFTY'}
    row=preview['contracts'][0]
    assert row['lot_size']==65 and row['contract_identifier'] is None and row['source_timestamp'] is None
    assert row['missing_fields']['contract_identifier']=='NOT_PUBLISHED_IN_RESPONSE'
    assert row['provenance']['source_hash']
    assert all(r['source_timestamp'] is None for r in preview['expiry_preview'])
    assert all(r['validation_status']!='VERIFIED_CALENDAR' for r in preview['expiry_preview'])


def test_bid_ask_fields_do_not_become_synthetic_quotes(tmp_path):
    from tests.test_nse_official_chain import complete_test_response, make_adapter, REQUEST
    root=Path(__file__).resolve().parents[2]
    registry=SourceRegistry.from_inventory(root/'backend/calendar_evidence_acquisition/parsed/nse_market_data_20261003.json',root)
    body=complete_test_response(registry)
    body['records']['data'][0]['CE'].pop('buyPrice1',None)
    body['filtered']['data']=deepcopy(body['records']['data'])
    r=make_adapter(tmp_path,registry,body).fetch_nse_option_chain(**REQUEST)
    assert r['status']=='OK' and r['rows'][0]['bid_price'] is None
    assert r['rows'][0]['unavailable_fields']['bid_price']=='NOT_PUBLISHED_IN_RESPONSE'
    body['records']['data'][0]['CE']['sellPrice1']=float('inf')
    body['filtered']['data']=deepcopy(body['records']['data'])
    rejected=make_adapter(tmp_path,registry,body).fetch_nse_option_chain(**REQUEST)
    assert rejected['status']=='DATA_UNAVAILABLE' and rejected['rows']==[]


def test_gate_validated_reports_still_need_manual_activation(tmp_path):
    from app.nse_official.controls import GATES
    from app.nse_official.core import sha
    raw=json.dumps(dict(status='PASS',complete=True,unexpected_failures=0,errors=0,timeouts=0)).encode()
    path=tmp_path/'review.json';path.write_bytes(raw)
    checks={k:dict(evidence_path=str(path),sha256=sha(raw),independently_reviewed=True) for k in GATES}
    r=production_gate(checks)
    assert r['eligible_for_review'] and r['PRODUCTION']=='BLOCKED' and not r['calendar_installed']
    path.write_bytes(b'{}')
    assert not production_gate(checks)['eligible_for_review']


def test_expanded_official_responses_map_only_observed_contracts(tmp_path):
    from app.nse_official.master import build_preview
    root=Path(__file__).resolve().parents[2]
    registry=SourceRegistry.from_inventory(root/'backend/calendar_evidence_acquisition/parsed/nse_completion_20261003.json',root)
    a=NSEAdapter(registry,NSEStore(tmp_path))
    preview=build_preview(a)
    assert preview['rejected_responses']==[]
    assert preview['coverage']==dict.fromkeys(('NIFTY','BANKNIFTY','FINNIFTY','MIDCPNIFTY'),140)
    assert len(preview['expiry_preview'])==30 and len(preview['contracts'])==560
    assert {r['valid_on_trade_date'] for r in preview['contracts']}=={'2026-10-01'}
    assert {r['option_type'] for r in preview['contracts']}=={'CE','PE'}
    assert all(r['lot_size']>0 and r['token'] is None for r in preview['contracts'])
    assert not preview['contract_universe_complete']
    assert a.resolve_expiry('AUTO',symbol='BANKNIFTY')['status']=='SOURCE_METADATA_REQUIRED'
    assert a.fetch_nse_historical_contract_data(symbol='SENSEX',instrument_type='OPTIDX',
        trade_date='2026-10-01',expiry='2026-10-27',option_type='CE')['status']=='SOURCE_METADATA_REQUIRED'


def test_acquisition_headers_and_validation_are_append_only(tmp_path):
    from app.nse_official.provenance import Response
    root=Path(__file__).resolve().parents[2]
    registry=SourceRegistry.from_inventory(root/'backend/calendar_evidence_acquisition/parsed/nse_market_data_20261003.json',root)
    def fetch(obs):
        return Response(b'{}',503,'2026-10-03T05:59:00Z',obs.endpoint,'application/json',
                        response_headers={'Content-Type':'application/json','Set-Cookie':'do-not-retain','Authorization':'do-not-retain'})
    a=NSEAdapter(registry,NSEStore(tmp_path),clock=lambda:datetime(2026,10,3,6,tzinfo=timezone.utc),transport=fetch)
    request=dict(symbol='NIFTY',instrument_type='OPTIDX',trade_date='2026-10-01',expiry='2026-10-06',option_type='CE')
    first=a.fetch_nse_historical_contract_data(**request)
    assert first['reason']=='HTTP_503'
    assert first['provenance']['response_headers']=={'Content-Type':'application/json'}
    old={p:p.read_bytes() for p in (tmp_path/'validations').glob('*.json')}
    a.fetch_nse_historical_contract_data(**request)
    assert all(p.read_bytes()==b for p,b in old.items())


def test_transport_failure_is_retained_without_invented_http_status(tmp_path):
    from app.nse_official.core import DataError
    root=Path(__file__).resolve().parents[2]
    registry=SourceRegistry.from_inventory(root/'backend/calendar_evidence_acquisition/parsed/nse_market_data_20261003.json',root)
    def failed(obs):raise DataError('HTTP_TRANSPORT_FAILED')
    a=NSEAdapter(registry,NSEStore(tmp_path),transport=failed)
    r=a.fetch_nse_option_chain(symbol='NIFTY',expiry='2026-10-06')
    saved=json.loads(next((tmp_path/'validations').glob('*.json')).read_text())
    assert saved['reason']=='HTTP_TRANSPORT_FAILED' and saved['response_status'] is None and saved['sha256'] is None
    assert r['auto_next']=='FAIL-CLOSED'
