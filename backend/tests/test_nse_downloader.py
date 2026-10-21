"""Synthetic official-page/ZIP fixtures; no live NSE or broker dependency."""
import csv
from datetime import date, datetime, timezone
import io
import json
from pathlib import Path
import zipfile

import pytest

from calendar_evidence_acquisition.nse_downloader import Acquisition, Failure, validate_archive
from calendar_evidence_acquisition.nse_discovery import PAGE, TITLE, discover, safe_url, json_records
from calendar_evidence_acquisition.static_archives import digest

DAY = date(2026, 10, 1)
URL = 'https://nsearchives.nseindia.com/published/example.zip'  # Synthetic observed link, never a URL template.
MEMBER = 'BhavCopy_NSE_FO_0_0_0_20261001_F_0000.csv'
FIELDS = ['TradDt','BizDt','Sgmt','Src','FinInstrmTp','FinInstrmId','TckrSymb','XpryDt',
          'FininstrmActlXpryDt','StrkPric','OptnTp','OpnPric','HghPric','LwPric','ClsPric',
          'LastPric','UndrlygPric','SttlmPric','OpnIntrst','ChngInOpnIntrst','TtlTradgVol']


def archive(**changes):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=FIELDS)
    writer.writeheader()
    for index, symbol in enumerate(('NIFTY','BANKNIFTY','FINNIFTY','MIDCPNIFTY')):
        row = dict.fromkeys(FIELDS, '1')
        row.update(TradDt='2026-10-01', BizDt='2026-10-01', Sgmt='FO', Src='NSE', FinInstrmTp='IDO',
                   FinInstrmId=str(index), TckrSymb=symbol, XpryDt='2026-10-27',
                   FininstrmActlXpryDt='2026-10-27', OptnTp='CE')
        row.update(changes)
        writer.writerow(row)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(MEMBER, output.getvalue())
    return buf.getvalue()


def proof(**changes):
    result = dict(page_url=PAGE, page_status=200, page_body=b'<official synthetic fixture>',
                  observed_at='2026-10-03T01:00:00Z', records=[dict(report_name=TITLE,
                  trading_date='2026-10-01', download_url=URL, method='DOM_LINK')])
    result.update(changes)
    return result


class Transport:
    def __init__(self, evidence=None, raw=None):
        self.evidence = evidence if evidence is not None else proof()
        self.raw = raw if raw is not None else archive()
        self.discoveries = self.downloads = 0
    def observe(self, requested):
        self.discoveries += 1
        if isinstance(self.evidence, Exception):
            raise self.evidence
        return self.evidence
    def download(self, observed, destination):
        self.downloads += 1
        destination.write_bytes(self.raw)
        return dict(url=observed['download_url'], status=200, content_type='application/zip',
                    filename=MEMBER+'.zip', downloaded_at='2026-10-03T01:01:00Z', redirects=[])
    def close(self):
        pass


def test_exact_semantic_report_discovery():
    p = proof()
    p['records'][0]['report_name'] = '  f&o -   UDIFF Common Bhavcopy Final (ZIP) '
    assert discover(p, DAY)['download_url'] == URL


@pytest.mark.parametrize('name', ['F&O Bhavcopy', TITLE+' old', 'NIFTY 50 historical prices', 'UDiFF Common Bhavcopy'])
def test_wrong_report_is_not_a_provenance_source(name):
    p = proof(); p['records'][0]['report_name'] = name
    with pytest.raises(Failure, match='SOURCE_METADATA_REQUIRED'):
        discover(p, DAY)


@pytest.mark.parametrize('url', [PAGE,'http://www.nseindia.com/file.zip','https://www.nseindia.com.evil.test/a',
                               'https://example.com/a','blob:https://www.nseindia.com/123',
                               'https://www.nseindia.com/a?token=x','https://www.nseindia.com:8443/a'])
def test_wrong_urls_are_rejected(url):
    with pytest.raises(Failure):
        safe_url(url, download=True)


def test_no_filename_to_url_generation():
    p = proof(); p['records'][0].pop('download_url'); p['records'][0]['filename'] = MEMBER+'.zip'
    with pytest.raises(Failure, match='SOURCE_METADATA_REQUIRED'):
        discover(p, DAY)


def test_missing_and_wrong_date_do_not_substitute_latest():
    with pytest.raises(Failure):
        discover(proof(), date(2026, 9, 30))


def test_completed_date_is_taken_from_listing_not_weekdays():
    selected = discover(proof(), None, clock=datetime(2026,10,3,tzinfo=timezone.utc))
    assert selected['trading_date'] == '2026-10-01'
    with pytest.raises(Failure):
        discover(proof(), None, clock=datetime(2026,10,1,tzinfo=timezone.utc))


@pytest.mark.parametrize('changes,reason', [({'TradDt':'2026-09-30'},'NSE_DATE_MISMATCH'),
    ({'BizDt':'2026-09-30'},'NSE_DATE_MISMATCH'),({'Src':'BSE'},'NSE_EXCHANGE_MISMATCH'),
    ({'Sgmt':'CM'},'NSE_SEGMENT_MISMATCH'),({'OpnPric':'NaN'},'NSE_NUMERIC_INVALID'),
    ({'XpryDt':''},'NSE_EXPIRY_INVALID')])
def test_content_validation(changes,reason):
    with pytest.raises(Failure,match=reason): validate_archive(archive(**changes),DAY)


@pytest.mark.parametrize('member', ['../'+MEMBER, '/'+MEMBER, 'folder/'+MEMBER])
def test_archive_paths_are_rejected(member):
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z: z.writestr(member,b'anything')
    with pytest.raises(Failure): validate_archive(buf.getvalue(),DAY)


@pytest.mark.parametrize('raw', [b'PKbroken',b'Index Name,Date,Open,High,Low,Close\nNIFTY 50,01 Oct 2026,1,1,1,1'])
def test_corrupt_or_ohlc_source_rejected(raw):
    with pytest.raises(Failure): validate_archive(raw,DAY)


def test_acquisition_preserves_proof_and_ingests_only_validated_bytes(tmp_path):
    transport=Transport(); job=Acquisition(tmp_path/'artifacts', transport)
    result=job.run(DAY)
    assert result['status']=='ACQUIRED_FOR_REVIEW'
    assert result['calendar_status']=='UNVERIFIED'
    sidecar=Path(result['archive_path']+'.json')
    meta=json.loads(sidecar.read_text())
    assert meta['provenance_url']==URL and meta['report_page_url']==PAGE
    assert meta['sha256']==digest(transport.raw)
    assert meta['http_status']==200
    assert (job.root/'evidence_ledger.json').exists()
    entry=json.loads((job.root/'evidence_ledger.json').read_text())['entries'][0]
    assert entry['acquisition_route']=='BROWSER'
    assert entry['source_metadata']['report_page_url']==PAGE
    assert entry['source_metadata']['acquisition_proof_sha256']==meta['acquisition_proof_sha256']
    assert job.run(DAY)['status']=='ALREADY_VERIFIED'
    assert transport.downloads==transport.discoveries==1


def test_dry_run_never_downloads_or_writes(tmp_path):
    transport=Transport(); root=tmp_path/'absent'
    result=Acquisition(root,transport).run(DAY,dry_run=True)
    assert result['status']=='DRY_RUN' and result['download_url']==URL
    assert transport.downloads==0 and not root.exists()


def test_missing_provenance_never_downloads_or_writes(tmp_path):
    transport=Transport(proof(records=[])); root=tmp_path/'absent'
    result=Acquisition(root,transport).run(DAY)
    assert result['reason']=='SOURCE_METADATA_REQUIRED'
    assert transport.downloads==0 and not (root/'evidence_ledger.json').exists()


def test_existing_unattributed_archive_not_redownloaded(tmp_path):
    root=tmp_path/'artifacts'; (root/'staging').mkdir(parents=True)
    local=root/'staging/BhavCopy_NSE_FO_20261001.csv.zip'; raw=archive(); local.write_bytes(raw)
    t=Transport(); result=Acquisition(root,t).run(DAY)
    assert result['reason']=='SOURCE_METADATA_REQUIRED'
    assert result['detail']=='EXISTING_ARCHIVE_ORIGIN_UNPROVEN'
    assert t.downloads==0 and local.read_bytes()==raw and not Path(str(local)+'.json').exists()


def test_expected_hash_mismatch_quarantines_without_ledger(tmp_path):
    root=tmp_path/'artifacts'; t=Transport()
    result=Acquisition(root,t).run(DAY,expected_sha256='0'*64)
    assert result['reason']=='NSE_HASH_MISMATCH'
    assert list((root/'nse/quarantine').glob('*.zip'))
    assert not (root/'evidence_ledger.json').exists()


@pytest.mark.parametrize('status', [403,429,404])
def test_permanent_page_failure_no_retries(tmp_path,status):
    t=Transport(proof(page_status=status)); pauses=[]
    result=Acquisition(tmp_path/'a',t,sleep=pauses.append).run(DAY)
    assert result['status']=='UNVERIFIED' and t.discoveries==1 and not pauses


def test_transient_errors_have_bounded_backoff(tmp_path):
    t=Transport(Failure('NSE_CONNECTION_FAILED',transient=True)); pauses=[]
    result=Acquisition(tmp_path/'a',t,sleep=pauses.append).run(DAY)
    assert t.discoveries==3 and pauses==[1,2]
    assert result['reason']=='SOURCE_METADATA_REQUIRED'
    assert result['detail']=='NSE_CONNECTION_FAILED'


def test_tampered_receipt_cannot_bypass_validation(tmp_path):
    t=Transport(); job=Acquisition(tmp_path/'a',t); result=job.run(DAY)
    Path(result['archive_path']).write_bytes(b'changed')
    result=job.run(DAY)
    assert result['status']=='UNVERIFIED' and t.downloads==1


def test_page_json_must_associate_label_date_url_in_one_object():
    data={'reports':[{'name':TITLE,'tradingDate':'2026-10-01','downloadUrl':URL},
                     {'name':'Other report','tradingDate':'2026-10-01','downloadUrl':'https://www.nseindia.com/other.zip'}]}
    records=json_records(data,'https://www.nseindia.com/observed-public-response')
    assert discover(proof(records=records),DAY)['download_url']==URL
    assert json_records({'name':TITLE,'children':{'date':'2026-10-01','url':URL}},PAGE)==[]


def test_ambiguous_official_links_do_not_pick_arbitrary_first():
    p=proof(); p['records'].append(dict(p['records'][0],download_url='https://www.nseindia.com/another.zip'))
    with pytest.raises(Failure): discover(p,DAY)


def test_timezone_latest_completed_boundary():
    before=datetime(2026,10,1,18,29,tzinfo=timezone.utc)
    after=datetime(2026,10,1,18,30,tzinfo=timezone.utc)
    with pytest.raises(Failure): discover(proof(),None,clock=before)
    assert discover(proof(),None,clock=after)['trading_date']=='2026-10-01'


@pytest.mark.parametrize('change', ['missing_header','no_targets','extra_member'])
def test_required_schema_and_target_validation(change):
    if change=='no_targets': raw=archive(TckrSymb='UNRELATED')
    else:
        raw=archive()
        with zipfile.ZipFile(io.BytesIO(raw)) as z: body=z.read(MEMBER)
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            z.writestr(MEMBER,body.replace(b'UndrlygPric',b'OtherPrice') if change=='missing_header' else body)
            if change=='extra_member': z.writestr('extra.csv',b'anything')
        raw=buf.getvalue()
    with pytest.raises(Failure): validate_archive(raw,DAY)


def test_invalid_download_quarantined_before_sidecar_and_ledger(tmp_path):
    root=tmp_path/'a'; t=Transport(raw=archive(TradDt='2026-09-30'))
    result=Acquisition(root,t).run(DAY)
    assert result['reason']=='NSE_DATE_MISMATCH'
    assert not (root/'evidence_ledger.json').exists()
    assert not list((root/'staging').glob('*.json'))
    assert next((root/'nse/quarantine').glob('*.zip')).read_bytes()==t.raw


@pytest.mark.parametrize('status', [403,429,500])
def test_download_http_policy(tmp_path,status):
    class HTTP(Transport):
        def download(self,selected,destination):
            value=super().download(selected,destination); value['status']=status; return value
    t=HTTP(); pauses=[]; result=Acquisition(tmp_path/'a',t,sleep=pauses.append).run(DAY)
    assert result['reason']==f'NSE_HTTP_{status}'
    assert t.downloads==(3 if status==500 else 1)
    assert pauses==([1,2] if status==500 else [])


def test_redirect_cannot_replace_discovered_source(tmp_path):
    class Redirect(Transport):
        def download(self,selected,destination):
            value=super().download(selected,destination)
            value['url']='https://www.nseindia.com/other.zip'; value['redirects']=[URL,value['url']]
            return value
    t=Redirect(); root=tmp_path/'a'; result=Acquisition(root,t).run(DAY)
    assert result['reason']=='NSE_REDIRECT_REQUIRES_REVIEW'
    assert not (root/'evidence_ledger.json').exists()


def test_racing_different_archive_is_not_overwritten(tmp_path):
    root=tmp_path/'a'; unexpected=b'pre-existing user bytes'
    class Race(Transport):
        def download(self,selected,destination):
            result=super().download(selected,destination)
            (root/'staging').mkdir(parents=True)
            (root/'staging'/result['filename']).write_bytes(unexpected)
            return result
    t=Race(); result=Acquisition(root,t).run(DAY)
    assert result['reason']=='NSE_ARTIFACT_CONFLICT'
    assert (root/'staging'/(MEMBER+'.zip')).read_bytes()==unexpected
    assert list((root/'nse/quarantine').glob('*.zip'))


def test_receipt_proof_tampering_blocks_idempotent_success(tmp_path):
    job=Acquisition(tmp_path/'a',Transport()); job.run(DAY)
    receipt=json.loads((job.root/'nse/receipts/2026-10-01.json').read_text())
    (job.root/receipt['proof_relative_path']).write_text('{}')
    assert job.run(DAY)['reason']=='NSE_RECEIPT_INVALID'


def test_browser_missing_dependency_is_structured_and_no_writes(tmp_path,monkeypatch):
    import builtins
    from calendar_evidence_acquisition.nse_browser import NSEBrowser
    original=builtins.__import__
    def guarded(name,*args,**kwargs):
        if name.startswith('playwright'): raise ImportError('missing')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',guarded)
    browser=NSEBrowser()
    try: result=Acquisition(tmp_path/'absent',browser).run(DAY,dry_run=True)
    finally: browser.close()
    assert result['reason']=='SOURCE_METADATA_REQUIRED'
    assert result['detail']=='PLAYWRIGHT_NOT_INSTALLED'
    assert not (tmp_path/'absent').exists()


@pytest.mark.parametrize('changes', [{'FinInstrmTp':'INDEX'},{'OptnTp':'BAD'}])
def test_instrument_and_option_types_are_validated(changes):
    with pytest.raises(Failure): validate_archive(archive(**changes),DAY)


def test_conflicting_contract_ids_cannot_enter_ledger(tmp_path):
    root=tmp_path/'a'; result=Acquisition(root,Transport(raw=archive(FinInstrmId='same'))).run(DAY)
    assert result['reason']=='NSE_CONTRACT_CONFLICT'
    assert not (root/'evidence_ledger.json').exists()


def test_existing_local_hash_checked_even_in_dry_run(tmp_path):
    root=tmp_path/'a'; (root/'staging').mkdir(parents=True)
    (root/'staging'/('BhavCopy_NSE_FO_20261001.csv.zip')).write_bytes(archive())
    t=Transport(); result=Acquisition(root,t).run(DAY,dry_run=True,expected_sha256='0'*64)
    assert result['reason']=='NSE_HASH_MISMATCH' and t.downloads==0


def test_deleted_discovery_body_cannot_be_replaced_by_receipt_claim(tmp_path):
    job=Acquisition(tmp_path/'a',Transport()); job.run(DAY)
    next((job.root/'nse/proofs').glob('*.html')).unlink()
    assert job.run(DAY)['status']=='UNVERIFIED'


@pytest.mark.parametrize('url,method,redirect,allowed', [(URL,'GET',None,True),
    ('https://evil.test/data','GET',None,False),(URL,'POST',None,False),
    (URL,'GET',object(),False)])
def test_browser_request_guard_enforces_read_only_official_origins(url,method,redirect,allowed):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from calendar_evidence_acquisition.nse_browser import NSEBrowser
    browser=NSEBrowser(); browser.policy=AsyncMock()
    request=SimpleNamespace(url=url,method=method,redirected_from=redirect,resource_type='document',is_navigation_request=lambda:True)
    route=SimpleNamespace(request=request,abort=AsyncMock(),continue_=AsyncMock())
    asyncio.run(browser.gate(route))
    assert route.continue_.await_count==int(allowed)
    assert route.abort.await_count==int(not allowed)


def test_browser_action_capture_aborts_before_download():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from calendar_evidence_acquisition.nse_browser import NSEBrowser
    async def run():
        browser=NSEBrowser(); browser.policy=AsyncMock(); browser.capture_day=DAY; browser.capture_event=asyncio.Event()
        request=SimpleNamespace(url=URL,method='GET',redirected_from=None,resource_type='fetch',is_navigation_request=lambda:False)
        route=SimpleNamespace(request=request,abort=AsyncMock(),continue_=AsyncMock())
        await browser.gate(route)
        assert browser.capture_event.is_set() and route.abort.await_count==1 and route.continue_.await_count==0
        assert browser.records[0]['download_url']==URL
    asyncio.run(run())


def test_browser_adapter_captures_semantic_dom_link_without_real_browser(monkeypatch):
    import sys,types
    from unittest.mock import AsyncMock
    from calendar_evidence_acquisition.nse_browser import NSEBrowser
    label=types.SimpleNamespace(wait_for=AsyncMock())
    inputs=types.SimpleNamespace(count=AsyncMock(return_value=0))
    page=types.SimpleNamespace(url=PAGE,set_default_timeout=lambda t:None,on=lambda *a:None,
          get_by_text=lambda *a:types.SimpleNamespace(first=label),locator=lambda *a:inputs,get_by_label=lambda *a:inputs,
          wait_for_timeout=AsyncMock(),evaluate=AsyncMock(return_value=proof()['records']),content=AsyncMock(return_value='<synthetic report>'))
    async def goto(url,**kwargs): page.url=url; return types.SimpleNamespace(status=200)
    page.goto=goto
    context=types.SimpleNamespace(route=AsyncMock(),new_page=AsyncMock(return_value=page))
    launched=types.SimpleNamespace(new_context=AsyncMock(return_value=context),close=AsyncMock())
    pw=types.SimpleNamespace(chromium=types.SimpleNamespace(launch=AsyncMock(return_value=launched)),stop=AsyncMock())
    module=types.ModuleType('playwright.async_api')
    module.async_playwright=lambda:types.SimpleNamespace(start=AsyncMock(return_value=pw))
    module.Error=type('PWError',(Exception,),{}); module.TimeoutError=type('PWTimeout',(module.Error,),{})
    monkeypatch.setitem(sys.modules,'playwright',types.ModuleType('playwright'))
    monkeypatch.setitem(sys.modules,'playwright.async_api',module)
    browser=NSEBrowser(); browser.policy=AsyncMock()
    try: result=browser.observe(DAY)
    finally: browser.close()
    assert discover(result,DAY)['download_url']==URL
    assert result['page_url']==PAGE and result['page_status']==200


def test_scheduler_forwards_dry_run_and_custom_evidence_root(monkeypatch):
    from calendar_evidence_acquisition import nse_job
    received=[]
    monkeypatch.setattr(nse_job,'downloader_main',lambda args:received.append(args) or 0)
    assert nse_job.main(['--dry-run','--artifacts','/tmp/isolated-evidence'])==0
    assert received==[['--latest-completed','--dry-run','--artifacts','/tmp/isolated-evidence']]


@pytest.mark.parametrize('http_status', [200,403,429,None])
def test_browser_download_uses_only_observed_http_metadata(tmp_path,http_status):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from calendar_evidence_acquisition.nse_browser import NSEBrowser
    source=tmp_path/'browser-download.zip'; source.write_bytes(archive())
    destination=tmp_path/'pending.zip'; handlers=[]
    async def save(path): Path(path).write_bytes(source.read_bytes())
    download=SimpleNamespace(url=URL,suggested_filename=MEMBER+'.zip',failure=AsyncMock(return_value=None),
                             path=AsyncMock(return_value=str(source)),save_as=save)
    class Pending:
        async def __aenter__(self):
            self.value=asyncio.Future(); self.value.set_result(download); return self
        async def __aexit__(self,*args): pass
    async def navigate(script,url):
        assert url==URL
        if http_status is not None:
            response=SimpleNamespace(url=URL,status=http_status,all_headers=AsyncMock(return_value={'content-type':'application/zip'}))
            for callback in handlers: callback(response)
    browser=NSEBrowser(); browser.policy=AsyncMock(); browser.records=proof()['records']
    browser.page=SimpleNamespace(on=lambda event,callback:handlers.append(callback),expect_download=lambda **kwargs:Pending(),evaluate=navigate)
    try:
        if http_status==200:
            result=browser.download(browser.records[0],destination)
            assert result['status']==200 and result['url']==URL and destination.read_bytes()==source.read_bytes()
        else:
            with pytest.raises(Failure): browser.download(browser.records[0],destination)
            assert not destination.exists()
    finally: browser.close()
