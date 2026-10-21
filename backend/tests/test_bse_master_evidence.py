"""Official BSE snapshot observations must not activate a production calendar."""
import csv
import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path
import zipfile
import pytest
from app.nse_official.calendar import OfficialSource

ROOT=Path(__file__).resolve().parents[2]
STAGE=ROOT/'artifacts/staging/own-exchange-direct-20261004'
MEMBER='BSE_EQD_CONTRACT_01102026.csv'


def source(stem):
    m=json.loads((STAGE/(stem+'.json')).read_text());raw=(ROOT/m['raw_path']).read_bytes()
    assert m['http_status']==200
    return OfficialSource(m['source_url'],raw,m['sha256'],m['retrieved_at'],True)


def inputs():
    m=json.loads((ROOT/'backend/calendar_evidence_acquisition/public_sources_20261002/bse_app_index.json').read_text())
    discovery=OfficialSource(m['url'],(ROOT/m['path']).read_bytes(),m['sha256'],m['retrieved_at'],True)
    return source('9789880bf792034443e3'),discovery,source('9b7b2e798dec2f9c37ba')


def toy(**changes):
    row=dict(FinInstrmId='123',TckrSymb='SENSEX',FinInstrmNm='IO',XpryDt='08-OCT-2026',StartDt='01-OCT-2026',EndDt='09-OCT-2026',MtrtyDt='',StrkPric='8000000',OptnTp='CE',MinLot='20',NewBrdLotQty='20',PrtdToTrad='0',DelFlg='A',CtrctTp='W',StockNm='EXPLICIT SERIES',DcmlstnPric='2')
    row.update(changes);buf=io.StringIO();w=csv.DictWriter(buf,fieldnames=list(row));w.writeheader();w.writerow(row);return buf.getvalue().encode()


def test_real_master_explicit_rows_are_observed_not_production_eligible():
    from calendar_evidence_acquisition.bse_master import observe_bse_master
    r=observe_bse_master(*inputs(),member=MEMBER)
    assert len(r['contracts'])==4051 and len(r['observed_expiries'])==21
    assert r['duplicates']==[] and r['conflicts']==[]
    assert r['counts_by_type']=={'OPTION':4048,'FUTURE':3}
    assert r['validated_production_expiries']==[] and r['complete_historical_calendar'] is False
    assert r['production_calendar']=='NOT_INSTALLED' and r['auto_next']=='FAIL-CLOSED'
    assert all(c['classification']=='OBSERVED_OFFICIAL_EXPIRY' and c['last_trading_at'] is None for c in r['contracts'])


def test_expiry_and_settlement_are_distinct_and_filler_is_not_maturity():
    from calendar_evidence_acquisition.bse_master import parse_master_csv
    r=parse_master_csv(toy())['contracts'][0]
    assert r['expiry']=='2026-10-08' and r['settlement_maturity_date']=='2026-10-09'
    assert r['raw_maturity_filler']=='' and r['strike']=='80000'
    assert r['cycle']=='WEEKLY' and r['source_timestamp'] is None
    assert r['last_trading_date']=='2026-10-08' and r['listed_at'] is None


@pytest.mark.parametrize('change',[{'XpryDt':''},{'XpryDt':'31-FEB-2026'},{'StrkPric':'NaN'},{'FinInstrmNm':'OPTFUT'},{'OptnTp':'XX'},{'CtrctTp':'UNKNOWN'},{'NewBrdLotQty':'0'},{'DelFlg':'?'},{'PrtdToTrad':'?'},{'DcmlstnPric':'4'},{'MtrtyDt':'08-OCT-2026'},{'StartDt':'09-OCT-2026'}])
def test_unknown_or_invalid_semantics_reject_without_inference(change):
    from calendar_evidence_acquisition.bse_master import parse_master_csv
    with pytest.raises(ValueError):parse_master_csv(toy(**change))


def test_duplicates_and_identifier_conflicts_are_reported():
    from calendar_evidence_acquisition.bse_master import parse_master_csv
    first=toy();r=parse_master_csv(first+first.splitlines(keepends=True)[1])
    assert len(r['contracts'])==1 and r['duplicates']==[3]
    r=parse_master_csv(first+toy(XpryDt='15-OCT-2026').splitlines(keepends=True)[1])
    assert r['status']=='CONFLICTING' and len(r['contracts'])==2 and r['conflicts'][0]['contract_identifier']=='123'


def test_other_symbols_and_expired_reserved_contracts_are_not_silently_relabelled():
    from calendar_evidence_acquisition.bse_master import parse_master_csv
    assert not parse_master_csv(toy(TckrSymb='BANKEX'))['contracts']
    r=parse_master_csv(toy(DelFlg='E',PrtdToTrad='1'))['contracts'][0]
    assert r['contract_state']=='EXPIRED' and r['reserved'] is True
    assert r['production_eligible'] is False


@pytest.mark.parametrize('problem',['sha','unobserved','broker','samples','discovery','format'])
def test_provenance_failure_never_yields_official_observations(problem):
    from calendar_evidence_acquisition.bse_master import observe_bse_master
    m,d,f=inputs()
    if problem=='sha':m=replace(m,sha256='0'*64)
    if problem=='unobserved':m=replace(m,independently_observed=False)
    if problem=='broker':m=replace(m,url='https://api.dhan.co/file.zip')
    if problem=='samples':m=f
    if problem=='discovery':d=replace(d,raw=b'no link',sha256=hashlib.sha256(b'no link').hexdigest())
    if problem=='format':f=replace(f,raw=b'not ZIP',sha256=hashlib.sha256(b'not ZIP').hexdigest())
    with pytest.raises(ValueError):observe_bse_master(m,d,f,member=MEMBER)


def test_archive_path_traversal_rejected():
    from calendar_evidence_acquisition.bse_master import observe_bse_master
    m,d,f=inputs();b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:z.writestr('../'+MEMBER,toy())
    raw=b.getvalue();m=replace(m,raw=raw,sha256=hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError):observe_bse_master(m,d,f,member=MEMBER)


def direct_report():
    return json.loads((ROOT/'backend/calendar_evidence_acquisition/parsed/own_exchange_direct_evidence_20261004.json').read_text())


def test_retained_source_hashes_and_observed_records_are_reproducible():
    import gzip
    report=direct_report()
    for s in report['sources']:
        assert hashlib.sha256((ROOT/s['raw_path']).read_bytes()).hexdigest()==s['sha256']
    b=report['bse_sensex'];raw=(ROOT/b['observations_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==b['observations_sha256']
    normalized=json.loads(gzip.decompress(raw))
    assert len(normalized['contracts'])==4051 and normalized['provider_listing_status']=='UNVERIFIED'
    assert normalized['observed_expiries']==[x['expiry'] for x in b['expiry_groups']]


def test_holidays_and_specification_are_observations_not_generated_expiries():
    r=direct_report();h=r['bse_holidays']
    assert len(h['observations'])==16 and h['observations'][11]['date']=='2026-10-02'
    assert r['bse_specification']['status']=='INDEPENDENT_REVIEW_REQUIRED'
    assert r['production_calendar']=='NOT_INSTALLED' and r['auto_next']=='FAIL-CLOSED'
    assert all(c['scope']=='BSE FOCUSED IT, not SENSEX or MCX' for c in r['excluded_circulars'])


def test_mcx_unavailability_is_based_on_actual_data_route_denials():
    r=direct_report();sources=[s for s in r['sources'] if s['exchange']=='MCX']
    assert {s['source_url'] for s in sources}=={'https://www.mcxindia.com/market-data/contract-available-for-trading','https://www.mcxindia.com/market-data/bhavcopy'}
    assert all(s['http_status']==403 for s in sources)
    assert all(not x['observed_expiries'] and not x['ready'] for x in r['instruments'] if x['exchange']=='MCX')
