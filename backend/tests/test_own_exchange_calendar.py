"""Own-exchange contracts: synthetic fixtures are never production evidence."""
from dataclasses import replace
from datetime import date
import hashlib
import json
import pytest
from calendar_evidence_acquisition.evidence_engine import robots_policy, BrowserUnavailable


@pytest.mark.parametrize('body', ['<!doctype html><html><body>application</body></html>', '<HTML>denied</HTML>', '\ufeff<html>shell</html>'])
def test_html_is_not_an_allow_all_robots_policy(body):
    with pytest.raises(BrowserUnavailable, match='ROBOTS_POLICY_INVALID'):
        robots_policy(200, body, 'BSE')


def test_retained_bse_shell_cannot_authorize_acquisition():
    from pathlib import Path
    raw=(Path(__file__).resolve().parents[2]/'artifacts/staging/own-exchange-review-20261003-network/df63a7008aeaf3b65029.body').read_text()
    with pytest.raises(BrowserUnavailable, match='ROBOTS_POLICY_INVALID'):
        robots_policy(200, raw, 'BSE')


def observed(exchange='BSE',initial_symbol='SENSEX',**changes):
    """Explicit toy CSV/schema; no claim this is an acquired exchange layout."""
    from app.nse_official.calendar import OfficialSource
    from calendar_evidence_acquisition.official_eod import Discovery, Report, FieldMap
    row=dict(day='2026-10-01',venue=exchange,segment='FO' if exchange=='BSE' else 'COM',symbol=initial_symbol,
             kind='OPTIDX' if exchange=='BSE' else 'OPTFUT',expiry='2026-10-06',maturity='2026-10-30',
             identifier='TEST-1',strike='100',side='CE',lot='10')
    row.update(changes)
    raw=(','.join(row)+'\n'+','.join(row.values())+'\n').encode()
    base='https://www.'+exchange.lower()+'india.com/'
    url=base+'fixture.csv';html=('<a href="'+url+'">test</a>').encode()
    discovery=Discovery(base,200,html,hashlib.sha256(html).hexdigest(),'2026-10-01T16:00:00Z')
    report=Report(exchange,row['segment'],date(2026,10,1),url,'TEST ONLY','fixture.csv',discovery,'OFFICIAL_EXCHANGE_CONTRACT')
    source=OfficialSource(url,raw,hashlib.sha256(raw).hexdigest(),'2026-10-01T16:00:00Z',True)
    fields=FieldMap('TEST ONLY','day','symbol','kind','expiry',maturity='maturity',identifier='identifier',exchange='venue',segment='segment',strike='strike',option_type='side')
    return report,source,fields


@pytest.mark.parametrize('exchange,symbol',[('BSE','SENSEX'),('MCX','NATURALGAS'),('MCX','NATGASMINI'),('MCX','CRUDEOIL'),('MCX','CRUDEOILM')])
def test_observed_contracts_are_never_a_complete_production_calendar(exchange,symbol):
    from app.nse_official.own_exchange import OwnExchangeProvider
    provider=OwnExchangeProvider(exchange)
    result=provider.normalize(*observed(exchange,symbol),lot_size_field='lot')
    row=result['contracts'][0]
    assert row['classification']=='OBSERVED_OFFICIAL_EXPIRY'
    assert row['expiry']=='2026-10-06' and row['contract_maturity_date']=='2026-10-30'
    assert row['source_symbol']==symbol and row['lot_size']==10
    assert row['last_trading_at'] is None
    assert not result['complete_contract_master']
    assert result['production_calendar']=='NOT_INSTALLED'
    preview=provider.preview('AUTO',symbol,'2026-10-02T04:00:00Z')
    assert preview['classification']==('HISTORICAL_RETAINED_ONLY' if exchange=='MCX' else 'MISSING_EVIDENCE')


@pytest.mark.parametrize('changes,reason',[
 ({'expiry':''},'DATE'),({'strike':'NaN'},'NUMERIC'),({'strike':'-1'},'NUMERIC'),
 ({'side':''},'OPTION_IDENTITY'),({'lot':'0'},'NUMERIC'),({'kind':'FUTIDX'},'CONTRACT_TYPE'),
 ({'symbol':'NIFTY'},'EXCHANGE'),({'venue':'NSE'},'EXCHANGE'),({'segment':'OTHER'},'SEGMENT'),
])
def test_invalid_contracts_fail_as_a_whole(changes,reason):
    from app.nse_official.own_exchange import OwnExchangeProvider
    with pytest.raises(ValueError,match=reason):
        OwnExchangeProvider('MCX').normalize(*observed('MCX','NATURALGAS',**changes),lot_size_field='lot')


def test_missing_optional_fields_are_not_invented():
    from app.nse_official.own_exchange import OwnExchangeProvider
    report,source,fields=observed(identifier='')
    r=OwnExchangeProvider('BSE').normalize(report,source,fields)['contracts'][0]
    assert r['lot_size'] is None and r['contract_identifier'] is None
    assert r['unavailable_reasons']['lot_size']=='FIELD_NOT_PUBLISHED'
    assert r['source_timestamp'] is None and r['data_date']=='2026-10-01'


@pytest.mark.parametrize('issue',['hash','host','unobserved','undiscovered'])
def test_provenance_rejections(issue):
    from app.nse_official.own_exchange import OwnExchangeProvider
    report,source,fields=observed()
    if issue=='hash':source=replace(source,sha256='0'*64)
    if issue=='host':source=replace(source,url='https://www.nseindia.com/fixture.csv')
    if issue=='unobserved':source=replace(source,independently_observed=False)
    if issue=='undiscovered':report=replace(report,discovery=replace(report.discovery,body=b'no link',sha256=hashlib.sha256(b'no link').hexdigest()))
    with pytest.raises(ValueError):OwnExchangeProvider('BSE').normalize(report,source,fields)


def reviewed(symbol='SENSEX',exchange='BSE',**changes):
    from test_official_expiry_calendar import evidence
    from app.nse_official.own_exchange import OwnExchangeCalendarEvidence
    e=evidence(symbol,exchange);m=json.loads(e.manifest);h=e.sources[0].sha256
    m['calendar_claims'].update(holiday_calendar=[h],holiday_adjustment_rule=[h])
    for entry in m['entries']:entry['classification']='OBSERVED_OFFICIAL_EXPIRY'
    m.update(changes);raw=json.dumps(m).encode()
    return OwnExchangeCalendarEvidence(raw,hashlib.sha256(raw).hexdigest(),e.source_artifact,e.verification_status,e.verified_by,e.verification_reference,e.sources)


@pytest.mark.parametrize('exchange,symbol',[('BSE','SENSEX'),('MCX','NATURALGAS'),('MCX','NATGASMINI'),('MCX','CRUDEOIL'),('MCX','CRUDEOILM')])
@pytest.mark.parametrize('want,expected',[('CURRENT','2026-10-06'),('AUTO','2026-10-06'),('NEXT','2026-10-13')])
def test_reviewed_own_exchange_resolution_stays_preview_only(exchange,symbol,want,expected):
    from app.nse_official.own_exchange import OwnExchangeProvider
    p=OwnExchangeProvider(exchange,(reviewed(symbol,exchange),))
    r=p.preview(want,symbol,'2026-10-02T04:00:00Z')
    if exchange == 'MCX':
        assert r['expiry'] is None and r['reason']=='CALENDAR_EXCHANGE_DISABLED'
        assert r['auto_next']=='DISABLED' and r['scope']=='OUT_OF_SCOPE'
        return
    assert r['expiry']==expected and r['classification']=='VALIDATED_PRODUCTION_EXPIRY'
    assert r['production_calendar']=='NOT_INSTALLED' and r['auto_next']=='FAIL-CLOSED'


@pytest.mark.parametrize('issue',['inferred','missing_holiday','stale','wrong_exchange','unreviewed'])
def test_inferred_missing_stale_and_other_exchange_never_resolve(issue):
    from app.nse_official.own_exchange import OwnExchangeProvider
    e=reviewed();m=json.loads(e.manifest)
    if issue=='inferred':m['entries'][0]['classification']='INFERRED/DERIVED'
    if issue=='missing_holiday':m['calendar_claims'].pop('holiday_calendar')
    if issue=='stale':m['max_age_seconds']=10
    raw=json.dumps(m).encode();e=replace(e,manifest=raw,manifest_sha256=hashlib.sha256(raw).hexdigest())
    if issue=='unreviewed':e=replace(e,verification_status='UNVERIFIED')
    p=OwnExchangeProvider('MCX' if issue=='wrong_exchange' else 'BSE',(e,))
    r=p.preview('AUTO','SENSEX','2026-10-02T04:00:00Z')
    assert r['status']=='DATA_UNAVAILABLE' and r['expiry'] is None


def test_holiday_shift_requires_explicit_claim_never_computes_date():
    from app.nse_official.own_exchange import OwnExchangeProvider
    e=reviewed();m=json.loads(e.manifest);entry=m['entries'][0]
    entry.update(nominal_expiry='2026-10-07',expiry='2026-10-06')
    def make():
        raw=json.dumps(m).encode();return replace(e,manifest=raw,manifest_sha256=hashlib.sha256(raw).hexdigest())
    assert OwnExchangeProvider('BSE',(make(),)).preview('AUTO','SENSEX','2026-10-02T04:00:00Z')['status']=='DATA_UNAVAILABLE'
    entry['claims']['holiday_adjustment']=[e.sources[0].sha256]
    p=OwnExchangeProvider('BSE',(make(),))
    assert p.preview('AUTO','SENSEX','2026-10-02T04:00:00Z')['expiry']=='2026-10-06'
    assert p.preview('CURRENT','SENSEX','2026-10-06T15:30:00+05:30')['expiry']=='2026-10-13'


@pytest.mark.parametrize('change',['duplicate','conflicting_strike','expired','future_observation'])
def test_duplicate_conflicting_and_invalid_interval_records(change):
    from app.nse_official.own_exchange import OwnExchangeProvider
    report,source,fields=observed()
    raw=source.raw
    if change=='duplicate':raw+=raw.splitlines(keepends=True)[1]
    if change=='conflicting_strike':raw+=raw.splitlines(keepends=True)[1].replace(b',100,',b',200,')
    if change=='expired':raw=raw.replace(b'2026-10-06',b'2026-09-30')
    if change=='future_observation':source=replace(source,retrieved_at='2026-09-30T16:00:00Z')
    source=replace(source,raw=raw,sha256=hashlib.sha256(raw).hexdigest())
    if change=='duplicate':
        result=OwnExchangeProvider('BSE').normalize(report,source,fields)
        assert len(result['contracts'])==1 and result['duplicate_rows']==[3]
    else:
        with pytest.raises(ValueError,match={'conflicting_strike':'CONFLICT','expired':'EXPIRED','future_observation':'FUTURE'}[change]):
            OwnExchangeProvider('BSE').normalize(report,source,fields)


def test_maturity_is_not_a_substitute_for_missing_expiry():
    from app.nse_official.own_exchange import OwnExchangeProvider
    with pytest.raises(ValueError):OwnExchangeProvider('BSE').normalize(*observed(expiry=''))


def test_policy_denials_and_plaintext_policy_are_preserved():
    for code in (403,429):
        with pytest.raises(BrowserUnavailable):robots_policy(code,'denied','MCX')
    policy=robots_policy(200,'User-agent: *\nDisallow: /private\n','MCX')
    assert not policy.can_fetch('ZeroHero','https://www.mcxindia.com/private')
