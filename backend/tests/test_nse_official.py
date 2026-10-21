"""Official observations stay distinct from calendars, live quotes and broker data."""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.nse_official import NSEAdapter, NSEStore, SourceRegistry, Response, freshness, compare_sources, select_source

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT/'backend/calendar_evidence_acquisition/parsed/nse_page_discovery_20261003.json'
NOW = datetime(2026,10,3,4,tzinfo=timezone.utc)
REQUEST = dict(symbol='NIFTY',instrument_type='OPTIDX',trade_date='2026-10-01',expiry='2026-10-06',option_type='CE')


@pytest.fixture
def registry(): return SourceRegistry.from_inventory(INVENTORY, ROOT)


@pytest.fixture
def raw(registry): return registry.find('historical', REQUEST).raw


def adapter(tmp_path, registry, response=None):
    return NSEAdapter(registry, NSEStore(tmp_path/'official'),
                      transport=(lambda observation: response) if response else None, clock=lambda: NOW)


def response(raw, endpoint=None, status=200, sha=None):
    return Response(raw=raw,status=status,received_timestamp=NOW.isoformat(),
                    endpoint=endpoint or json.loads(INVENTORY.read_text())['historical_contract']['data_source_url'],
                    content_type='application/json',sha256=sha)


def test_real_official_response_normalizes_all_70_rows_without_faking_quote_time(tmp_path,registry):
    a=adapter(tmp_path,registry);r=a.fetch_nse_historical_contract_data(**REQUEST)
    assert r['status']=='OK' and r['source']=='NSE_OFFICIAL'
    assert r['response_status']==200 and len(r['rows'])==70
    assert r['data_timestamp']=='2026-10-01'
    assert r['source_timestamp'] is None and r['data_age_seconds'] is None
    assert r['freshness_status']=='DATA_UNAVAILABLE'
    assert r['fetch_timestamp']!=r['data_timestamp']
    assert {row['expiry'] for row in r['rows']}=={'2026-10-06'}
    assert all(row['source']=='NSE_OFFICIAL' for row in r['rows'])
    assert all(c['token_if_available'] is None and c['lot_size_if_available']==65 for c in r['contracts'])
    assert any(row['price_status']=='NO_TRADES_REPORTED' for row in r['rows'])
    cached=list((a.store.root/'expiry_observations').glob('*.json'))
    assert len(cached)==1
    assert json.loads(cached[0].read_text())['rows'][0]['expiry']=='2026-10-06'


@pytest.mark.parametrize('body,reason', [(b'not json','INVALID_JSON'),(b'{"data": []}','EMPTY_DATASET'),
    (b'{"data": null}','INVALID_SCHEMA'),(b'{"data": [], "data": []}','DUPLICATE_JSON_KEY')])
def test_invalid_payload_keeps_raw_and_rejects_entire_dataset(tmp_path,registry,body,reason):
    a=adapter(tmp_path,registry,response(body));r=a.fetch_nse_historical_contract_data(**REQUEST)
    assert r['status']=='DATA_UNAVAILABLE' and r['reason']==reason and r['rows']==[]
    assert (a.store.root/'raw'/hashlib.sha256(body).hexdigest()).read_bytes()==body
    assert not list((a.store.root/'datasets').glob('**/*.json')) if (a.store.root/'datasets').exists() else True


@pytest.mark.parametrize('field,value,reason', [
    ('FH_EXPIRY_DT',None,'MISSING_EXPIRY'),('FH_EXPIRY_DT','07-Oct-2026','CONTRACT_MISMATCH'),
    ('FH_SYMBOL','BANKNIFTY','CONTRACT_MISMATCH'),('FH_TIMESTAMP','02-Oct-2026','TIMESTAMP_MISMATCH'),
    ('FH_TIMESTAMP_ORDER','2026-10-02T18:30:00Z','TIMESTAMP_MISMATCH'),
    ('FH_OPEN_INT','bad','INVALID_NUMERIC'),('FH_TOT_TRADED_QTY',-1,'INVALID_NUMERIC'),
    ('FH_LAST_TRADED_PRICE',float('inf'),'INVALID_JSON'),('FH_STRIKE_PRICE',True,'INVALID_NUMERIC'),
])
def test_one_invalid_row_rejects_whole_response(tmp_path,registry,raw,field,value,reason):
    body=json.loads(raw);body['data'][0][field]=value
    a=adapter(tmp_path,registry,response(json.dumps(body).encode()));r=a.fetch_nse_historical_contract_data(**REQUEST)
    assert r['status']=='DATA_UNAVAILABLE' and r['reason']==reason and not r['rows']


def test_duplicates_and_missing_required_fields_are_not_dropped(tmp_path,registry,raw):
    body=json.loads(raw);body['data'].append(copy.deepcopy(body['data'][0]))
    a=adapter(tmp_path,registry,response(json.dumps(body).encode()))
    assert a.fetch_nse_historical_contract_data(**REQUEST)['reason']=='DUPLICATE_CONTRACT'
    del body['data'][-1];del body['data'][1]['FH_CHANGE_IN_OI']
    a=adapter(tmp_path,registry,response(json.dumps(body).encode()))
    assert a.fetch_nse_historical_contract_data(**REQUEST)['reason']=='MISSING_FIELD'


@pytest.mark.parametrize('status',[302,403,429,500])
def test_http_error_never_normalizes(tmp_path,registry,raw,status):
    r=adapter(tmp_path,registry,response(raw,status=status)).fetch_nse_historical_contract_data(**REQUEST)
    assert r['status']=='DATA_UNAVAILABLE' and r['reason']==f'HTTP_{status}' and not r['rows']


def test_hash_redirect_and_guessed_url_rejected(tmp_path,registry,raw):
    assert adapter(tmp_path,registry,response(raw,sha='0'*64)).fetch_nse_historical_contract_data(**REQUEST)['reason']=='SHA256_MISMATCH'
    assert adapter(tmp_path,registry,response(raw,endpoint='https://evil.test/data')).fetch_nse_historical_contract_data(**REQUEST)['reason']=='ENDPOINT_MISMATCH'
    assert adapter(tmp_path,registry).fetch_nse_historical_contract_data(**dict(REQUEST,expiry='2026-10-07'))['status']=='SOURCE_METADATA_REQUIRED'


def test_idempotency_atomic_dataset_and_broker_overwrite_blocked(tmp_path,registry):
    a=adapter(tmp_path,registry);r=a.fetch_nse_historical_contract_data(**REQUEST)
    first=a.store.ingest(r);second=a.store.ingest(r)
    assert first['status']=='INGESTED' and second['status']=='ALREADY_INGESTED'
    assert first['dataset_key']==second['dataset_key']
    assert len(a.store.read(first['dataset_key'])['rows'])==70
    r['rows'][0]['oi']+=1
    assert a.store.ingest(r)['reason']=='NORMALIZED_DATA_MISMATCH'
    r['source']='DHAN'
    assert a.store.ingest(r)['reason']=='OFFICIAL_SOURCE_REQUIRED'


def test_revision_cannot_silently_replace_same_dataset(tmp_path,registry,raw):
    a=adapter(tmp_path,registry);one=a.store.ingest(a.fetch_nse_historical_contract_data(**REQUEST))
    body=json.loads(raw);body['data'][0]['FH_OPEN_INT']+=1
    b=adapter(tmp_path,registry,response(json.dumps(body).encode()))
    two=b.store.ingest(b.fetch_nse_historical_contract_data(**REQUEST))
    assert two['status']=='DATA_UNAVAILABLE' and two['reason']=='DATASET_REVISION_CONFLICT'
    assert a.store.read(one['dataset_key'])['sha256']==hashlib.sha256(raw).hexdigest()


def test_expiry_observations_are_not_an_installed_historical_calendar(tmp_path,registry):
    a=adapter(tmp_path,registry)
    r=a.fetch_nse_expiry_selector(symbol='NIFTY',instrument_type='OPTIDX',year=2026)
    assert r['status']=='OK' and len(r['rows'])==50
    assert all(row['status']=='OBSERVED_NOT_CALENDAR_VERIFIED' for row in r['rows'])
    assert a.resolve_expiry('AUTO',**REQUEST)['status']=='SOURCE_METADATA_REQUIRED'
    assert a.resolve_expiry('NEXT',**REQUEST)['auto_next']=='FAIL-CLOSED'


def test_unobserved_option_chain_and_split_bhavcopy_url_stay_unavailable(tmp_path,registry):
    a=adapter(tmp_path,registry)
    assert a.fetch_nse_option_chain(symbol='NIFTY',expiry='2026-10-06')['status']=='SOURCE_METADATA_REQUIRED'
    assert a.fetch_nse_fno_reports_metadata()['status']=='OK'
    r=a.fetch_nse_bhavcopy_metadata(trade_date='2026-10-01')
    assert r['status']=='SOURCE_METADATA_REQUIRED' and r['reason']=='COMPLETE_DOWNLOAD_URL_NOT_OBSERVED'


@pytest.mark.parametrize('source,expected', [('2026-10-03T03:59:59+00:00','LIVE'),
    ('2026-10-03T03:59:30+00:00','RECENT'),('2026-10-03T03:58:00+00:00','STALE'),
    ('2026-10-03T04:00:01+00:00','DATA_UNAVAILABLE'),(None,'DATA_UNAVAILABLE'),
    ('2026-10-03T04:00:00','DATA_UNAVAILABLE')])
def test_age_requires_actual_aware_source_timestamp(source,expected):
    r=freshness(source,NOW.isoformat(),live_seconds=2,stale_seconds=60)
    assert r['status']==expected
    if expected=='DATA_UNAVAILABLE':assert r['data_age_seconds'] is None


def test_discrepancy_never_mutates_sources_and_checks_identity():
    n=dict(source='NSE_OFFICIAL',symbol='NIFTY',expiry='2026-10-06',instrument_type='OPTIDX',
           option_type='CE',strike=22000,exchange='NSE',timeframe='1d',data_timestamp='2026-10-01',ltp=100,oi=10)
    b=dict(n,source='DHAN',ltp=102,oi=12)
    before=copy.deepcopy((n,b));r=compare_sources(n,b)
    assert r['status']=='DATA_DISCREPANCY' and r['differences']['ltp']==-2
    assert (n,b)==before
    b['expiry']='2026-10-13'
    assert compare_sources(n,b)['reason']=='CONTRACT_MISMATCH'


def test_official_primary_explicit_provider_and_labelled_whole_dataset_fallback():
    n=dict(source='NSE_OFFICIAL',status='OK',rows=[{'source':'NSE_OFFICIAL'}])
    b=dict(source='DHAN',status='OK',rows=[{'source':'DHAN'}])
    assert select_source(n,[b],allow_fallback=True)['source']=='NSE_OFFICIAL'
    n.update(status='DATA_UNAVAILABLE',reason='NO_DATA')
    assert select_source(n,[b])['status']=='DATA_UNAVAILABLE'
    r=select_source(n,[b],allow_fallback=True)
    assert r['source']=='DHAN' and r['source_role']=='FALLBACK'
    assert r['rows'][0]['source']=='DHAN' and r['rows'][0]['source_role']=='FALLBACK'
    assert select_source(n,[b],provider='NSE')['status']=='DATA_UNAVAILABLE'


def test_ingestion_requires_original_receipt_not_only_raw_hash(tmp_path,registry):
    a=adapter(tmp_path,registry);r=a.fetch_nse_historical_contract_data(**REQUEST)
    r['provenance']['discovery_sources']=[]
    assert a.store.ingest(r)['reason']=='RECEIPT_MISMATCH'


def test_report_listing_rejects_bad_row_instead_of_silently_dropping_it(tmp_path,registry):
    obs=registry.find('reports',{})
    body=json.loads(obs.raw);body['CurrentDay'].append(None)
    r=adapter(tmp_path,registry,response(json.dumps(body).encode(),endpoint=obs.endpoint)).fetch_nse_fno_reports_metadata()
    assert r['status']=='DATA_UNAVAILABLE' and r['rows']==[]


def test_fallback_cannot_change_requested_contract():
    n=dict(source='NSE_OFFICIAL',status='DATA_UNAVAILABLE',contract_request=REQUEST)
    b=dict(source='DHAN',status='OK',contract_request=dict(REQUEST,expiry='2026-10-13'),rows=[{'source':'DHAN'}])
    assert select_source(n,[b],allow_fallback=True)['status']=='DATA_UNAVAILABLE'


def test_missing_contract_count_fails_closed(tmp_path,registry):
    r=adapter(tmp_path,registry).fetch_nse_historical_contract_data(**REQUEST,expected_row_count=71)
    assert r['reason']=='INCOMPLETE_RESPONSE' and r['rows']==[]


def test_session_boundary_uses_indian_trading_date(tmp_path,registry,raw):
    # A day-stamped response is not a quote; UTC date must not shift its session.
    r=response(raw)
    r=Response(r.raw,r.status,'2026-09-30T23:00:00Z',r.endpoint,r.content_type)
    result=adapter(tmp_path,registry,r).fetch_nse_historical_contract_data(**REQUEST)
    assert result['status']=='OK' and result['source_timestamp'] is None


@pytest.mark.parametrize('policy,status,expected', [
    (b'User-agent: *\nAllow: /',200,'OK'),
    (b'User-agent: *\nDisallow: /api/',200,'ROBOTS_DENIED'),
    (b'Forbidden',403,'ROBOTS_POLICY_UNAVAILABLE'),
    (b'Rate limited',429,'ROBOTS_POLICY_UNAVAILABLE'),
])
def test_http_transport_checks_policy_before_exact_observed_endpoint(tmp_path,registry,policy,status,expected):
    from app.nse_official.transport import OfficialHTTPTransport
    from app.nse_official.core import DataError
    obs=registry.find('historical',REQUEST);store=NSEStore(tmp_path/'http')
    transport=OfficialHTTPTransport(registry,store);calls=[]
    def get(url):
        calls.append(url)
        if url.endswith('/robots.txt'):return Response(policy,status,NOW.isoformat(),url,'text/plain')
        assert url==obs.endpoint
        return response(obs.raw,endpoint=url)
    transport._get=get
    if expected=='OK':
        result=transport(obs)
        assert result.endpoint==obs.endpoint and result.transport_evidence['result']=='ALLOWED'
        assert calls==['https://www.nseindia.com/robots.txt',obs.endpoint]
    else:
        with pytest.raises(DataError,match=expected):transport(obs)
        assert calls==['https://www.nseindia.com/robots.txt']


@pytest.mark.parametrize('status',[302,403,429,200])
def test_http_get_is_tls_validated_get_only_and_never_follows_redirects(monkeypatch,tmp_path,registry,status):
    import ssl
    from app.nse_official import transport as module
    from app.nse_official.core import DataError
    obs=registry.find('historical',REQUEST);calls=[]
    class Reply:
        def __init__(self):self.status=status;self.sent=False
        def getheader(self,key,default=None):return {'Content-Length':'2','Content-Type':'application/json'}.get(key,default)
        def read1(self,n):
            if self.sent:return b''
            self.sent=True;return b'{}'
    class Connection:
        def __init__(self,host,timeout,context):
            assert host=='www.nseindia.com' and timeout==10
            assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED
        def request(self,method,target,headers):
            assert method=='GET' and 'Authorization' not in headers and 'Cookie' not in headers
            calls.append(target)
        def getresponse(self):return Reply()
        def close(self):pass
    monkeypatch.setattr(module.http.client,'HTTPSConnection',Connection)
    transport=module.OfficialHTTPTransport(registry,NSEStore(tmp_path))
    if status==302:
        with pytest.raises(DataError,match='REDIRECT_REJECTED'):transport._get(obs.endpoint)
    else:assert transport._get(obs.endpoint).status==status
    assert len(calls)==1


def test_unregistered_transport_observation_never_attempts_network(tmp_path,registry):
    from dataclasses import replace
    from app.nse_official.transport import OfficialHTTPTransport
    from app.nse_official.core import DataError
    transport=OfficialHTTPTransport(registry,NSEStore(tmp_path))
    def forbidden(url):pytest.fail('Unregistered URLs must fail before any request')
    transport._get=forbidden
    obs=replace(registry.find('historical',REQUEST),endpoint='https://www.nseindia.com/guessed')
    with pytest.raises(DataError,match='EXACT_OBSERVED_ENDPOINT_REQUIRED'):transport(obs)


def test_contract_master_retains_missing_lot_without_inventing_one(tmp_path,registry,raw):
    body=json.loads(raw);del body['data'][0]['FH_MARKET_LOT']
    r=adapter(tmp_path,registry,response(json.dumps(body).encode())).fetch_nse_historical_contract_data(**REQUEST)
    assert r['status']=='OK'
    assert sum(c['lot_size_if_available'] is None for c in r['contracts'])==1


def test_storage_rejects_tampered_published_raw_bytes(tmp_path,registry):
    from app.nse_official.core import DataError
    a=adapter(tmp_path,registry);r=a.fetch_nse_historical_contract_data(**REQUEST)
    key=a.store.ingest(r)['dataset_key']
    (a.store.root/'raw'/r['sha256']).write_bytes(b'tampered test fixture')
    with pytest.raises(DataError,match='SHA256_MISMATCH'):a.store.read(key)


def test_explicit_broker_missing_data_never_uses_official_substitution():
    n=dict(source='NSE_OFFICIAL',status='OK',rows=[{'source':'NSE_OFFICIAL'}])
    assert select_source(n,[],provider='ANGELONE',allow_fallback=True)['status']=='DATA_UNAVAILABLE'


def test_cached_quote_cannot_remain_live_forever():
    n=dict(source='NSE_OFFICIAL',status='OK',rows=[{'source':'NSE_OFFICIAL'}],
           source_timestamp='2026-10-03T03:00:00Z',received_timestamp='2026-10-03T03:00:01Z')
    assert select_source(n,[],for_live=True,as_of=NOW)['status']=='DATA_UNAVAILABLE'
    n.update(source_timestamp='2026-10-03T03:59:59Z',received_timestamp='2026-10-03T04:00:00Z')
    assert select_source(n,[],for_live=True,as_of=NOW)['freshness_status']=='LIVE'
    n['received_timestamp']='2026-10-03T04:00:01Z'
    assert select_source(n,[],for_live=True,as_of=NOW)['status']=='DATA_UNAVAILABLE'


@pytest.mark.parametrize('threshold',[float('nan'),float('inf'),-1])
def test_invalid_age_thresholds_are_not_a_live_data_bypass(threshold):
    with pytest.raises(ValueError,match='INVALID_AGE_THRESHOLDS'):
        freshness(NOW,NOW,stale_seconds=threshold)


def test_cli_default_validation_is_nonpublishing_and_ingestion_is_idempotent(tmp_path,capsys):
    from app.nse_official.__main__ import main
    argv=['--repository',str(ROOT),'--inventory',str(INVENTORY),'--symbol','NIFTY',
          '--instrument-type','OPTIDX','--trade-date','2026-10-01','--expiry','2026-10-06','--option-type','CE']
    assert main(argv)==0
    assert json.loads(capsys.readouterr().out)['ingestion']['status']=='NOT_REQUESTED'
    assert main(argv+['--ingest','--output',str(tmp_path/'data')])==0
    assert json.loads(capsys.readouterr().out)['ingestion']['status']=='INGESTED'
    assert main(argv+['--ingest','--output',str(tmp_path/'data')])==0
    assert json.loads(capsys.readouterr().out)['ingestion']['status']=='ALREADY_INGESTED'


def test_registry_refuses_operator_claims_without_independent_source_metadata(tmp_path):
    from app.nse_official.core import DataError
    raw=b'{}';p=tmp_path/'raw.json';p.write_bytes(raw)
    meta=dict(source='NSE',sha256=hashlib.sha256(raw).hexdigest(),independently_observed=False)
    (tmp_path/'sidecar.json').write_text(json.dumps(meta))
    inventory=dict(sources=[dict(staging_path='raw.json',sidecar_path='sidecar.json',sha256=meta['sha256'],source_url='https://www.nseindia.com/')])
    (tmp_path/'inventory.json').write_text(json.dumps(inventory))
    with pytest.raises(DataError,match='SOURCE_METADATA_REQUIRED'):
        SourceRegistry.from_inventory(tmp_path/'inventory.json',tmp_path)


def test_registry_refuses_changed_source_bytes(tmp_path):
    from app.nse_official.core import DataError
    (tmp_path/'raw.json').write_bytes(b'changed')
    (tmp_path/'sidecar.json').write_text(json.dumps(dict(sha256='0'*64)))
    inventory=dict(sources=[dict(staging_path='raw.json',sidecar_path='sidecar.json',sha256='0'*64)])
    (tmp_path/'inventory.json').write_text(json.dumps(inventory))
    with pytest.raises(DataError,match='SOURCE_HASH_MISMATCH'):
        SourceRegistry.from_inventory(tmp_path/'inventory.json',tmp_path)


def test_dataset_envelope_cannot_disagree_with_validated_rows(tmp_path,registry):
    a=adapter(tmp_path,registry);r=a.fetch_nse_historical_contract_data(**REQUEST)
    r['data_timestamp']='2026-10-02'
    assert a.store.ingest(r)['reason']=='DATASET_ENVELOPE_MISMATCH'


def test_interrupted_publish_retains_raw_and_can_be_retried(tmp_path,registry,monkeypatch):
    import app.nse_official.store as module
    a=adapter(tmp_path,registry);r=a.fetch_nse_historical_contract_data(**REQUEST)
    publish=module.publish
    def fail_dataset(path,raw):
        if path.parent.name=='datasets':raise OSError('synthetic write failure')
        return publish(path,raw)
    monkeypatch.setattr(module,'publish',fail_dataset)
    assert a.store.ingest(r)['status']=='DATA_UNAVAILABLE'
    assert (a.store.root/'raw'/r['sha256']).exists()
    assert not list((a.store.root/'datasets').glob('*.json'))
    monkeypatch.setattr(module,'publish',publish)
    assert a.store.ingest(r)['status']=='INGESTED'


def test_concurrent_duplicate_ingestion_publishes_once(tmp_path,registry):
    from concurrent.futures import ThreadPoolExecutor
    a=adapter(tmp_path,registry);r=a.fetch_nse_historical_contract_data(**REQUEST)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(a.store.ingest,[r,r]))
    assert sorted(x['status'] for x in results)==['ALREADY_INGESTED','INGESTED']
    assert len(list((a.store.root/'datasets').glob('*.json')))==1


def test_processing_time_is_recorded_after_transport_completion(tmp_path,registry,raw):
    from datetime import timedelta
    times=iter([NOW,NOW+timedelta(seconds=2)])
    received=NOW+timedelta(seconds=1)
    a=NSEAdapter(registry,NSEStore(tmp_path),clock=lambda:next(times),transport=lambda obs:
                 Response(raw,200,received.isoformat(),obs.endpoint,'application/json'))
    r=a.fetch_nse_historical_contract_data(**REQUEST)
    assert r['status']=='OK'
    assert r['processing_timestamp']>r['received_timestamp']
