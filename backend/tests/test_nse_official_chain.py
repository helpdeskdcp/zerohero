"""Real NSE chain evidence is retained even when stale/incomplete; never fill legs."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.nse_official import NSEAdapter, NSEStore, SourceRegistry, Response

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT/'backend/calendar_evidence_acquisition/parsed/nse_market_data_20261003.json'
NOW = datetime(2026,10,3,5,tzinfo=timezone.utc)
REQUEST = dict(symbol='NIFTY',expiry='2026-10-06')


@pytest.fixture
def registry():return SourceRegistry.from_inventory(INVENTORY,ROOT)


def make_adapter(tmp_path,registry,body=None):
    def transport(obs):
        return Response(json.dumps(body).encode(),200,'2026-10-03T04:59:59Z',obs.endpoint,'application/json')
    return NSEAdapter(registry,NSEStore(tmp_path),clock=lambda:NOW,transport=transport if body else None)


def complete_test_response(registry):
    body=json.loads(registry.find('option_chain',REQUEST).raw)
    # A deliberately small mocked response, using unchanged published price values.
    row=next(r for r in body['records']['data']
             if all(r[s]['identifier'] and r[s]['lastPrice']>0 for s in ('CE','PE')))
    body['records']['data']=[copy.deepcopy(row)]
    body['records']['timestamp']='03-Oct-2026 10:29:58'
    body['filtered']['data']=copy.deepcopy(body['records']['data'])
    for s in ('CE','PE'):
        body['filtered'][s]={'totOI':row[s]['openInterest'],'totVol':row[s]['totalTradedVolume']}
    return body


def test_actual_official_chain_is_stale_and_has_missing_contracts(tmp_path,registry):
    a=make_adapter(tmp_path,registry)
    r=a.fetch_nse_option_chain(**REQUEST)
    assert r['status']=='DATA_UNAVAILABLE' and r['reason']=='INCOMPLETE_CHAIN_CONTRACT'
    assert r['rows']==[] and r['freshness_status']=='STALE'
    assert r['source_timestamp']=='2026-10-01T10:10:00+00:00'
    assert (a.store.root/'raw'/r['sha256']).is_file()
    assert r['auto_next']=='FAIL-CLOSED'


def test_current_selector_is_observation_not_historical_calendar(tmp_path,registry):
    r=make_adapter(tmp_path,registry).fetch_nse_expiry_selector(symbol='NIFTY',instrument_type='OPTIDX')
    assert r['status']=='OK' and len(r['rows'])==18
    assert r['rows'][0]['expiry']=='2026-10-06' and r['auto_next']=='FAIL-CLOSED'


def test_valid_mock_chain_has_real_source_clock_and_unavailable_ohlc(tmp_path,registry):
    body=complete_test_response(registry)
    r=make_adapter(tmp_path,registry,body).fetch_nse_option_chain(**REQUEST)
    assert r['status']=='OK' and len(r['rows'])==2
    assert r['data_age_seconds']==1 and r['freshness_status']=='LIVE'
    assert r['current_age_seconds']==2
    assert all(x['open'] is None and x['source']=='NSE_OFFICIAL' for x in r['rows'])
    assert all(c['lot_size_if_available'] is None and c['token_if_available'] is None for c in r['contracts'])


@pytest.mark.parametrize('mutation,reason',[
    ('expiry','CONTRACT_MISMATCH'),('symbol','CONTRACT_MISMATCH'),('missing','INCOMPLETE_CHAIN_CONTRACT'),
    ('numeric','INVALID_NUMERIC'),('duplicate','DUPLICATE_CONTRACT'),('totals','CHAIN_TOTAL_MISMATCH'),
    ('future','FUTURE_SOURCE_TIMESTAMP'),('stale','STALE_SOURCE'),('filtered','CHAIN_FILTER_MISMATCH'),
    ('zero_quote','QUOTE_UNAVAILABLE'),
])
def test_chain_rejects_entire_invalid_snapshot(tmp_path,registry,mutation,reason):
    body=complete_test_response(registry);row=body['records']['data'][0]
    if mutation=='expiry':row['CE']['expiryDate']='13-10-2026'
    elif mutation=='symbol':row['CE']['underlying']='BANKNIFTY'
    elif mutation=='missing':row['CE']['identifier']=None
    elif mutation=='numeric':row['CE']['openInterest']='bad'
    elif mutation=='zero_quote':row['CE']['lastPrice']=0
    elif mutation=='duplicate':body['records']['data'].append(copy.deepcopy(row))
    elif mutation=='totals':body['filtered']['CE']['totOI']+=1
    elif mutation=='future':body['records']['timestamp']='03-Oct-2026 10:30:01'
    elif mutation=='stale':body['records']['timestamp']='01-Oct-2026 15:40:00'
    elif mutation=='filtered':body['filtered']['data']=[]
    if mutation!='filtered':body['filtered']['data']=copy.deepcopy(body['records']['data'])
    r=make_adapter(tmp_path,registry,body).fetch_nse_option_chain(**REQUEST)
    assert r['status']=='DATA_UNAVAILABLE' and r['reason']==reason and not r['rows']


def test_chain_explicit_expiry_must_have_own_observed_request(tmp_path,registry):
    r=make_adapter(tmp_path,registry).fetch_nse_option_chain(symbol='NIFTY',expiry='2026-10-13')
    assert r['status']=='SOURCE_METADATA_REQUIRED'
