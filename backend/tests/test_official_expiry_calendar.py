"""Synthetic reviewed evidence exercises code, never installs an exchange calendar."""
import hashlib
import json
from dataclasses import replace
from datetime import datetime
import pytest
from app.nse_official.calendar import OfficialCalendarEvidence, OfficialCalendars, OfficialSource
from app.hist_options.provider_calendar import ProviderCalendars


def evidence(symbol='NIFTY',exchange='NSE',**changes):
    raw=b'TEST ONLY: explicit listing/cutoff/cycle fixture, not exchange evidence'
    url={'NSE':'https://www.nseindia.com/','BSE':'https://www.bseindia.com/','MCX':'https://www.mcxindia.com/'}[exchange]
    digest=hashlib.sha256(raw).hexdigest()
    source=OfficialSource(url,raw,digest,'2026-10-01T04:00:00Z',True)
    entries=[dict(expiry=d,listed_at='2026-10-01T04:00:00Z',last_trading_at=d+'T10:00:00Z',
                  cycles=c,claims={k:[digest] for k in ('expiry','listed_at','last_trading_at','cycles')})
             for d,c in [('2026-10-06',['WEEKLY']),('2026-10-13',['WEEKLY']),('2026-10-27',['MONTHLY'])]]
    m=dict(schema_version='official-exchange-calendar/v1',calendar_version='TEST-ONLY-v1',provider=exchange+'_OFFICIAL',
           exchange=exchange,underlying=symbol,instrument='OPTION',instrument_type='OPTIDX' if exchange!='MCX' else 'OPTFUT',
           source=url,source_sha256=digest,published_at='2026-10-01T04:00:00Z',retrieved_at='2026-10-01T04:00:00Z',
           effective_from='2026-10-01T04:00:00Z',effective_to='2026-10-28T00:00:00Z',max_age_seconds=30*86400,
           complete=True,calendar_claims={k:[digest] for k in ('completeness','contract_mapping','published_at','effective_interval')},entries=entries)
    m.update(changes);manifest=json.dumps(m).encode()
    return OfficialCalendarEvidence(manifest,hashlib.sha256(manifest).hexdigest(),raw,'VERIFIED','test reviewer','test fixture only',(source,))


def resolve(e=None,want='CURRENT',at='2026-10-02T04:00:00Z',symbol='NIFTY',cycle=None):
    return OfficialCalendars(() if e is None else (e,)).preview(want,symbol,datetime.fromisoformat(at),cycle=cycle)


@pytest.mark.parametrize('symbol,exchange',[('NIFTY','NSE'),('BANKNIFTY','NSE'),('FINNIFTY','NSE'),('MIDCPNIFTY','NSE'),('SENSEX','BSE'),('NATURALGAS','MCX'),('CRUDEOIL','MCX')])
@pytest.mark.parametrize('want,expected',[('CURRENT','2026-10-06'),('AUTO','2026-10-06'),('NEXT','2026-10-13'),('2026-10-27','2026-10-27')])
def test_explicit_dates_current_and_next_are_scoped(symbol,exchange,want,expected):
    r=resolve(evidence(symbol,exchange),want,symbol=symbol)
    if exchange == 'MCX':
        assert r['status']=='DATA_UNAVAILABLE' and r['expiry'] is None
        assert r['reason']=='CALENDAR_EXCHANGE_DISABLED' and r['auto_next']=='DISABLED'
        return
    assert r['status']=='OK' and r['expiry']==expected
    assert r['provenance']['exchange']==exchange and r['production_calendar']=='NOT_INSTALLED'


@pytest.mark.parametrize('at,expected',[('2026-10-06T09:59:59Z','2026-10-06'),('2026-10-06T15:30:00+05:30','2026-10-13'),('2026-10-06T10:00:01Z','2026-10-13')])
def test_exact_evidenced_cutoff_is_exclusive(at,expected):
    assert resolve(evidence(),at=at)['expiry']==expected


def test_monthly_does_not_mean_last_weekday_or_last_listed_date():
    r=resolve(evidence(),cycle='MONTHLY')
    assert r['expiry']=='2026-10-27' and r['provenance']['cycles']==['MONTHLY']
    m=json.loads(evidence().manifest);m['entries'][0]['cycles']=None
    assert resolve(evidence(entries=m['entries']),cycle='MONTHLY')['status']=='DATA_UNAVAILABLE'


@pytest.mark.parametrize('changes,reason',[
 ({'complete':False},'CALENDAR_INCOMPLETE'),
 ({'max_age_seconds':60},'CALENDAR_STALE'),
 ({'calendar_claims':{}},'CALENDAR_CLAIM_EVIDENCE_REQUIRED'),
 ({'provider':'DHAN'},'CALENDAR_EVIDENCE_UNAVAILABLE'),
 ({'effective_to':'2026-10-02T04:00:00Z'},'CALENDAR_EVIDENCE_UNAVAILABLE'),
 ({'published_at':'2026-10-03T04:00:00Z'},'CALENDAR_EVIDENCE_UNAVAILABLE'),
 ({'retrieved_at':'2026-09-30T04:00:00Z'},'CALENDAR_RETRIEVAL_INVALID'),
])
def test_incomplete_stale_future_wrong_provider_fail_closed(changes,reason):
    r=resolve(evidence(**changes))
    assert r['status']=='DATA_UNAVAILABLE' and r['reason']==reason and r['expiry'] is None


def test_missing_evidence_and_unobserved_date_do_not_resolve():
    assert resolve()['status']=='DATA_UNAVAILABLE'
    assert resolve(evidence(),want='2026-10-20')['reason']=='CALENDAR_EXPIRY_UNAVAILABLE'


@pytest.mark.parametrize('change', ['unreviewed','hash','external_host','wrong_exchange','independent','cutoff_claim'])
def test_official_provenance_cannot_be_bypassed(change):
    e=evidence()
    if change=='unreviewed':e=replace(e,verification_status='UNVERIFIED')
    if change=='hash':e=replace(e,sources=(replace(e.sources[0],sha256='0'*64),))
    if change=='external_host':e=replace(e,sources=(replace(e.sources[0],url='https://example.com/'),))
    if change=='wrong_exchange':e=evidence(exchange='BSE')
    if change=='independent':e=replace(e,sources=(replace(e.sources[0],independently_observed=False),))
    if change=='cutoff_claim':
        m=json.loads(e.manifest);del m['entries'][0]['claims']['last_trading_at'];e=evidence(entries=m['entries'])
    assert resolve(e)['status']=='DATA_UNAVAILABLE'


def test_exchange_evidence_never_becomes_broker_listing_evidence():
    with pytest.raises(ValueError,match='CALENDAR_EVIDENCE_UNAVAILABLE'):
        ProviderCalendars((evidence(),)).resolve('AUTO','DHAN','NIFTY',datetime.fromisoformat('2026-10-02T04:00:00Z'))


def test_real_current_selectors_cannot_authorize_calendar():
    from pathlib import Path
    from app.nse_official.provenance import SourceRegistry
    root=Path(__file__).resolve().parents[2]
    registry=SourceRegistry.from_inventory(root/'backend/calendar_evidence_acquisition/parsed/nse_completion_20261003.json',root)
    assert len([r for r in registry.observations if r.kind=='live_selector'])==4
    assert resolve()['reason']=='CALENDAR_EVIDENCE_UNAVAILABLE'


@pytest.mark.parametrize('value',[False,None,'true',1])
def test_independent_observation_requires_literal_true(value):
    e=evidence();e=replace(e,sources=(replace(e.sources[0],independently_observed=value),))
    assert resolve(e)['status']=='DATA_UNAVAILABLE'


def test_mcx_night_cutoff_and_month_boundary_are_literal_evidence():
    e=evidence('NATURALGAS','MCX');m=json.loads(e.manifest)
    m['entries'][0]['last_trading_at']='2026-10-06T23:55:00+05:30'
    e=evidence('NATURALGAS','MCX',entries=m['entries'])
    for at in ['2026-10-06T18:24:59Z','2026-10-06T18:25:00Z']:
        r=resolve(e,at=at,symbol='NATURALGAS')
        assert r['expiry'] is None and r['reason']=='CALENDAR_EXCHANGE_DISABLED'
        assert r['auto_next']=='DISABLED'
    assert resolve(e,at='2026-09-30T18:30:00Z',symbol='NATURALGAS')['status']=='DATA_UNAVAILABLE'


def test_freshness_limit_includes_boundary_but_never_extends_validity():
    e=evidence(max_age_seconds=86400)
    assert resolve(e)['status']=='OK'
    assert resolve(e,at='2026-10-02T04:00:01Z')['reason']=='CALENDAR_STALE'
    assert resolve(e,at='2026-10-28T00:00:00Z')['status']=='DATA_UNAVAILABLE'


def test_claims_and_normalized_manifest_are_hash_bound():
    e=evidence();raw=e.manifest.replace(b'2026-10-06',b'2026-10-07')
    assert resolve(replace(e,manifest=raw))['reason']=='CALENDAR_MANIFEST_HASH_MISMATCH'


def test_monthly_next_and_shifted_weekly_dates_are_not_recalculated():
    m=json.loads(evidence().manifest)
    shifted=dict(m['entries'][0],expiry='2026-10-19',last_trading_at='2026-10-19T10:00:00Z')
    later=dict(m['entries'][-1],expiry='2026-11-23',last_trading_at='2026-11-23T10:00:00Z')
    e=evidence(entries=[shifted,m['entries'][-1],later])
    assert resolve(e)['expiry']=='2026-10-19'
    assert resolve(e,want='CURRENT',cycle='MONTHLY')['expiry']=='2026-10-27'
    assert resolve(e,want='NEXT',cycle='MONTHLY')['expiry']=='2026-11-23'
    later['listed_at']='2026-10-03T04:00:00Z'
    assert resolve(evidence(entries=[shifted,m['entries'][-1],later]),want='NEXT',cycle='MONTHLY')['reason']=='CALENDAR_EXPIRY_UNAVAILABLE'


def test_retained_official_rule_sources_do_not_install_a_calendar():
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    p=root/'backend/calendar_evidence_acquisition/parsed/nse_official_calendar_evidence_20261003.json'
    data=json.loads(p.read_text())
    assert data['counts']==dict(validated_expiry_observations=30,corroborated_annexure_cycle_entries=8,fully_verified_resolvable_entries=0)
    assert data['production_calendar']=='NOT_INSTALLED' and data['auto_next']=='FAIL-CLOSED'
    for source in data['sources']:
        if 'raw_path' in source:assert hashlib.sha256((root/source['raw_path']).read_bytes()).hexdigest()==source['sha256']
    text=(root/data['circular']['text_path']).read_bytes()
    assert hashlib.sha256(text).hexdigest()==data['circular']['text_sha256']
    assert b'June 25, 2025' in text and b'July 31, 2025 EOD' in text
    assert all(r['last_trading_at'] is None and r['status']=='UNVERIFIED' for r in data['observations'])
    candidate=json.loads((root/data['master_candidate']).read_text())
    assert candidate['expiry_dates_decoded'] is False
