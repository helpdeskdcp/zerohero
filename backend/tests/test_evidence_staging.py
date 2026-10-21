"""Operational staging contracts; all archives are synthetic, never production evidence."""
import io
import json
import zipfile

import pytest

from calendar_evidence_acquisition.evidence_engine import EvidenceEngine, main, robots_policy, BrowserUnavailable
from test_evidence_engine import RAW, NAME, staged


def test_dry_run_parses_without_writing_any_artifacts(tmp_path):
    engine, file = staged(tmp_path)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = engine.inspect(file)
    assert result['expiry_dates_iso'] == ['2026-09-08']
    assert result['status'] == 'UNVERIFIED'
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('raw', [b'PKbroken', RAW.replace(b'NIFTY', b'UNSUPPORTED')])
def test_rejected_candidates_never_enter_ledger(tmp_path, raw):
    engine, file = staged(tmp_path, raw)
    result = engine.ingest(file)
    assert result['reason'] in {'PARSER_REJECTED', 'NO_TARGET_RECORDS'}
    assert not engine.ledger.exists()
    assert (engine.root / result['raw_path']).read_bytes() == raw


@pytest.mark.parametrize('exchange,symbol', [('BSE', 'SENSEX'), ('MCX', 'NATGASMINI')])
def test_schema_detection_preserves_expiry_separate_from_maturity(tmp_path, exchange, symbol):
    raw = ('Trading Date,Symbol,Instrument,Expiry Date,Contract Maturity Date,Contract ID\n'
           f'2026-09-03,{symbol},OPT,2026-09-24,2026-09-30,7\n').encode()
    engine, file = staged(tmp_path, raw, name='download.csv', exchange=exchange,
                          segment='COM' if exchange == 'MCX' else 'FO',
                          provenance_url=f'https://www.{exchange.lower()}india.com/download.csv')
    if exchange == "MCX":
        with pytest.raises(ValueError, match="CALENDAR_EXCHANGE_DISABLED"):
            engine.ingest(file)
        assert not engine.ledger.exists()
        return
    result = engine.ingest(file)
    inventory = engine._verify_entry(result)
    assert inventory['observations'][0]['expiry_date'] == '2026-09-24'
    assert inventory['observations'][0]['contract_maturity_date'] == '2026-09-30'
    assert result['status'] == 'UNVERIFIED'


def test_status_never_promotes_staging_or_provider_claims(tmp_path):
    engine, file = staged(tmp_path, status='VERIFIED', provider='DHAN')
    engine.ingest(file)
    report = engine.status()
    assert len(report['combinations']) == 28
    assert report['counts']['UNVERIFIED'] == 12
    assert report['counts']['DISABLED'] == 16
    assert report['production_calendar'] == 'NOT INSTALLED'
    assert report['auto_next'] == 'FAIL-CLOSED'
    rows = [r for r in report['combinations'] if r['instrument'] == 'NIFTY']
    for row in rows:
        assert row['candidate_expiry_dates'] == ['2026-09-08']
        assert row['official_observed_expiry_dates'] == []
        assert row['qualifying_source_count'] == 0
        assert row['missing_required_date_ranges']
        assert row['provider_listing_status'] == 'UNVERIFIED'


def test_empty_status_is_read_only_and_shows_unknown_product_ranges(tmp_path):
    engine = EvidenceEngine(tmp_path / 'absent')
    report = engine.status()
    assert not engine.root.exists()
    mini = next(r for r in report['combinations'] if r['instrument'] == 'CRUDEOILMINI')
    assert mini['required_range'] is None
    assert mini['status']=='DISABLED' and mini['blockers']==[]
    assert mini['pending_requirements']==[]


def test_cli_flags_continue_after_bad_file_and_report_failure(tmp_path, capsys):
    engine, file = staged(tmp_path)
    bad = engine.staging / 'bad.csv'
    bad.write_bytes(b'invalid')
    assert main(['--staging', str(engine.staging), '--ingest']) == 2
    assert engine.ledger.exists()  # Valid file still ingested.
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(output) == 2


def test_cli_dry_run_never_creates_ledger(tmp_path, capsys):
    engine, file = staged(tmp_path)
    assert main(['--staging', str(engine.staging), '--dry-run']) == 0
    assert not engine.ledger.exists()
    assert json.loads(capsys.readouterr().out)['dry_run'] is True


def test_cli_status_reports_28_combinations_without_files(tmp_path, capsys):
    assert main(['--artifacts', str(tmp_path / 'absent'), '--status']) == 0
    assert len(json.loads(capsys.readouterr().out)['combinations']) == 28
    assert not (tmp_path / 'absent').exists()


def xlsx(raw=RAW):
    """Minimal OOXML fixture with inline text; no third-party spreadsheet dependency."""
    from xml.sax.saxutils import escape
    rows = raw.decode().splitlines()
    sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    for i, row in enumerate(rows, 1):
        sheet += f'<row r="{i}">'
        for j, value in enumerate(row.split(',')):
            sheet += f'<c r="{chr(65+j)}{i}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
        sheet += '</row>'
    sheet += '</sheetData></worksheet>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as archive:
        archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="data" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', sheet)
    return buf.getvalue()


@pytest.mark.parametrize('outer_zip', [False, True])
def test_xlsx_ingestion_hashes_original_bytes_without_extracting_files(tmp_path, outer_zip):
    raw = xlsx()
    if outer_zip:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('table.xlsx', raw)
        raw = buffer.getvalue()
    engine, file = staged(tmp_path, raw, name='data.zip' if outer_zip else 'data.xlsx')
    result = engine.ingest(file)
    assert result['expiry_dates_iso'] == ['2026-09-08']
    assert (engine.root / result['raw_path']).read_bytes() == raw
    assert engine._verify_entry(result)['source_sha256'] == result['sha256']
    assert len(list(engine.staging.iterdir())) == 2  # Original + metadata only.


def test_ambiguous_expiry_headers_fail_closed(tmp_path):
    raw = b'Trading Date,Symbol,Instrument,Expiry Date,EXPDATE\n2026-09-03,SENSEX,OPT,2026-09-24,2026-09-25\n'
    engine, file = staged(tmp_path, raw, name='bse.csv', exchange='BSE', provenance_url='https://www.bseindia.com/data.csv')
    result = engine.ingest(file)
    assert result['reason'] == 'PARSER_REJECTED'
    assert not engine.ledger.exists()


def test_filename_and_rows_can_establish_context_but_not_source(tmp_path):
    engine, file = staged(tmp_path)
    sidecar = file.with_name(file.name + '.json')
    meta = json.loads(sidecar.read_text())
    del meta['exchange']
    del meta['trading_date']
    sidecar.write_text(json.dumps(meta))
    assert engine.ingest(file)['trading_date'] == '2026-09-03'


def rewrite_xlsx(raw, replacements):
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(result, 'w') as target:
        for name in source.namelist():
            target.writestr(name, replacements.pop(name, source.read(name)))
        for name, body in replacements.items():
            target.writestr(name, body)
    return result.getvalue()


@pytest.mark.parametrize('epoch1904', [False, True])
def test_xlsx_date_styles_use_declared_epoch_not_csv_numeric_guesses(tmp_path, epoch1904):
    from datetime import date
    raw = xlsx()
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        sheet = source.read('xl/worksheets/sheet1.xml').decode()
        workbook = source.read('xl/workbook.xml').decode()
    serial = (date(2026, 9, 8) - date(1904, 1, 1)).days if epoch1904 else (date(2026, 9, 8) - date(1899, 12, 30)).days
    sheet = sheet.replace('<c r="F2" t="inlineStr"><is><t>2026-09-08</t></is></c>', f'<c r="F2" s="0"><v>{serial}</v></c>')
    if epoch1904:
        workbook = workbook.replace('<sheets>', '<workbookPr date1904="1"/><sheets>')
    raw = rewrite_xlsx(raw, {'xl/worksheets/sheet1.xml': sheet, 'xl/workbook.xml': workbook,
          'xl/styles.xml': '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cellXfs><xf numFmtId="14"/></cellXfs></styleSheet>'})
    engine, file = staged(tmp_path, raw, name='dates.xlsx')
    assert engine.ingest(file)['expiry_dates_iso'] == ['2026-09-08']


@pytest.mark.parametrize('modification', ['formula', 'two_sheets', 'external_link', 'dtd'])
def test_spreadsheet_ambiguity_or_active_content_is_rejected(tmp_path, modification):
    raw = xlsx()
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        sheet = source.read('xl/worksheets/sheet1.xml').decode()
        workbook = source.read('xl/workbook.xml').decode()
    replacements = {}
    if modification == 'formula':
        replacements['xl/worksheets/sheet1.xml'] = sheet.replace('<c r="F2" t="inlineStr">', '<c r="F2" t="inlineStr"><f>TODAY()</f>')
    elif modification == 'two_sheets':
        replacements['xl/workbook.xml'] = workbook.replace('</sheets>', '<sheet name="extra" sheetId="2" r:id="rId2"/></sheets>')
    elif modification == 'external_link':
        replacements['xl/externalLinks/externalLink1.xml'] = '<externalLink/>'
    else:
        replacements['xl/workbook.xml'] = '<!DOCTYPE workbook [<!ENTITY x "bad">]>' + workbook
    engine, file = staged(tmp_path, rewrite_xlsx(raw, replacements), name='rejected.xlsx')
    assert engine.ingest(file)['reason'] == 'PARSER_REJECTED'
    assert not engine.ledger.exists()


def test_conflict_status_is_scoped_to_exchange_underlying(tmp_path):
    engine, file = staged(tmp_path)
    engine.ingest(file)
    file.write_bytes(RAW.replace(b'2026-09-08', b'2026-09-15'))
    engine.ingest(file)
    report = engine.status()
    assert report['counts']['CONFLICTING'] == 2  # Same exchange conflict affects both providers' evidence plans.
    assert all(r['status'] == ('DISABLED' if r['exchange']=='MCX' else 'UNVERIFIED') for r in report['combinations'] if r['instrument'] != 'NIFTY')
    assert all(len(r['sha256_evidence']) == 2 for r in report['combinations'] if r['instrument'] == 'NIFTY')


def test_out_of_scope_observations_do_not_count_as_required_coverage(tmp_path):
    raw = RAW.replace(b'2026-09-03', b'2026-06-30')
    engine, file = staged(tmp_path, raw, name='older.csv', trading_date='2026-06-30')
    engine.ingest(file)
    rows = [r for r in engine.status()['combinations'] if r['instrument'] == 'NIFTY']
    assert all(r['candidate_source_count'] == 0 for r in rows)


def test_status_detects_corrupt_evidence_instead_of_reporting_green(tmp_path):
    engine, file = staged(tmp_path)
    entry = engine.ingest(file)
    (engine.root / entry['raw_path']).write_bytes(b'altered')
    with pytest.raises(ValueError, match='RETAINED_SOURCE_HASH_MISMATCH'):
        engine.status()


def test_incomplete_provenance_never_enters_ledger(tmp_path):
    engine, file = staged(tmp_path, report_name='')
    with pytest.raises(ValueError, match='REPORT_PROVENANCE_REQUIRED'):
        engine.ingest(file)
    assert not engine.ledger.exists()


@pytest.mark.parametrize('status,body', [(403, ''), (429, ''), (500, ''),
                                        (200, 'User-agent: *\nCrawl-delay: 10')])
def test_browser_refuses_unavailable_or_rate_restricted_robots_policy(status, body):
    with pytest.raises(BrowserUnavailable):
        robots_policy(status, body, 'NSE')


def test_browser_honors_robots_exclusion():
    policy = robots_policy(200, 'User-agent: *\nDisallow: /private/', 'NSE')
    assert not policy.can_fetch('*', 'https://www.nseindia.com/private/archive.zip')
    assert policy.can_fetch('*', 'https://www.nseindia.com/public/')


def test_unknown_filename_uses_literal_row_date_with_explicit_exchange(tmp_path):
    engine, file = staged(tmp_path, name='export.csv', trading_date=None)
    assert engine.ingest(file)['trading_date'] == '2026-09-03'


def test_multiple_trading_dates_cannot_be_inferred_as_one(tmp_path):
    raw = RAW + RAW.splitlines(keepends=True)[1].replace(b'2026-09-03', b'2026-09-04')
    engine, file = staged(tmp_path, raw, name='export.csv', trading_date=None)
    with pytest.raises(ValueError, match='SINGLE_TRADING_DATE_REQUIRED'):
        engine.ingest(file)
    assert not engine.ledger.exists()


@pytest.mark.parametrize('exchange,symbol', [('NSE', s) for s in ('NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY')] +
                         [('BSE', s) for s in ('SENSEX', 'BANKEX')] +
                         [('MCX', s) for s in ('NATURALGAS', 'NATGASMINI', 'CRUDEOIL', 'CRUDEOILMINI', 'GOLD', 'GOLDMINI', 'SILVER', 'SILVERMINI')])
def test_each_registered_derivative_is_retained_without_provider_inference(tmp_path, exchange, symbol):
    raw = RAW.replace(b'NIFTY', symbol.encode()).replace(b',NSE,FO,', f',{exchange},{"COM" if exchange == "MCX" else "FO"},'.encode())
    engine, file = staged(tmp_path, raw, name='export.csv', exchange=exchange,
                          segment='COM' if exchange == 'MCX' else 'FO',
                          provenance_url=f'https://www.{exchange.lower()}india.com/export.csv')
    if exchange == 'MCX':
        with pytest.raises(ValueError, match='CALENDAR_EXCHANGE_DISABLED'):
            engine.ingest(file)
        assert not engine.ledger.exists()
        return
    entry = engine.ingest(file)
    assert entry['expiry_observations'][0]['underlying'] == symbol
    assert entry['provider_listing_status'] == 'UNVERIFIED'
    assert engine._verify_entry(entry)['observations'][0]['provider'] is None


@pytest.mark.parametrize('archive_robots_status,archive_robots_body', [(200, ''), (403, ''), (200, 'User-agent: *\nDisallow: /')])
def test_normal_browser_respects_each_origins_robots_without_network(tmp_path, monkeypatch, archive_robots_status, archive_robots_body):
    import asyncio
    import sys
    import types
    from datetime import date
    from unittest.mock import AsyncMock
    from calendar_evidence_acquisition.evidence_engine import playwright_download
    from test_evidence_engine import URL
    archive = tmp_path / 'download'
    archive.write_bytes(RAW)
    events = []
    page = types.SimpleNamespace(url='https://www.nseindia.com/reports', set_default_timeout=lambda t: None)
    async def get(url, **kwargs):
        events.append(('robots', url))
        assert kwargs['max_redirects'] == 0 and kwargs['timeout'] == 10000
        on_archive = 'nsearchives' in url
        return types.SimpleNamespace(status=archive_robots_status if on_archive else 200,
                                     text=AsyncMock(return_value=archive_robots_body if on_archive else ''))
    async def goto(url, **kwargs):
        events.append(('visit', url))
        return types.SimpleNamespace(status=200)
    async def click(**kwargs):
        events.append(('download', URL))
    anchor = types.SimpleNamespace(get_attribute=AsyncMock(return_value=URL), click=click)
    page.goto = goto
    page.wait_for_selector = AsyncMock()
    page.locator = lambda selector: types.SimpleNamespace(all=AsyncMock(return_value=[anchor]))
    page.content = AsyncMock(return_value='<a href="' + URL + '">archive</a>')
    page.on = lambda *args: None
    class PendingDownload:
        async def __aenter__(self):
            self.value = asyncio.Future()
            self.value.set_result(types.SimpleNamespace(url=URL, suggested_filename=NAME,
                 failure=AsyncMock(return_value=None), path=AsyncMock(return_value=archive)))
            return self
        async def __aexit__(self, *args):
            pass
    page.expect_download = lambda **kwargs: PendingDownload()
    context = types.SimpleNamespace(request=types.SimpleNamespace(get=get), route=AsyncMock(),
                  new_page=AsyncMock(return_value=page), close=AsyncMock())
    browser = types.SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    pw = types.SimpleNamespace(chromium=types.SimpleNamespace(launch=AsyncMock(return_value=browser)))
    class Manager:
        async def __aenter__(self):
            return pw
        async def __aexit__(self, *args):
            pass
    module = types.ModuleType('playwright.async_api')
    module.async_playwright = Manager
    module.TimeoutError = type('PlaywrightTimeout', (Exception,), {})
    module.Error = type('PlaywrightError', (Exception,), {})
    monkeypatch.setitem(sys.modules, 'playwright', types.ModuleType('playwright'))
    monkeypatch.setitem(sys.modules, 'playwright.async_api', module)
    if archive_robots_status != 200 or archive_robots_body:
        with pytest.raises(BrowserUnavailable, match='ROBOTS_'):
            playwright_download(exchange='NSE', trading_date=date(2026, 9, 3), report_page=page.url)
        assert not any(e[0] == 'download' for e in events)
    else:
        assert playwright_download(exchange='NSE', trading_date=date(2026, 9, 3), report_page=page.url)['raw'] == RAW
        assert events.index(('visit', 'https://www.nseindia.com/')) < events.index(('download', URL))
    assert [e[1] for e in events if e[0] == 'robots'] == ['https://www.nseindia.com/robots.txt', 'https://nsearchives.nseindia.com/robots.txt']
    assert browser.new_context.call_args.kwargs['user_agent'] == 'ZeroHero-public-evidence/1'
