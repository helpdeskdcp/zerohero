"""Retained official facts must remain reproducible without calendar promotion."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from calendar_evidence_acquisition.bse_master import observe_bse_master
from test_bse_master_evidence import inputs, MEMBER

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'backend/calendar_evidence_acquisition/parsed/nse_bse_remaining_evidence_20261005.json'


def report():
    return json.loads(REPORT.read_text())


def test_official_document_has_literal_discovery_chain_and_intact_bytes():
    """A genuine link and raw-response hashes, not URL patterns, bind this source."""
    r = report()
    for source in r['acquisitions']:
        raw = (ROOT / source['raw_path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == source['sha256']
        assert len(raw) == source['bytes']
        assert source['redirects_followed'] is False
    source = r['nse_consolidated']['source']
    parent = (ROOT / source['discovery_parent']).read_bytes()
    assert hashlib.sha256(parent).hexdigest() == source['discovery_parent_sha256']
    assert source['source_url'].encode() in parent
    assert source['discovery_method'] == 'LITERAL_OFFICIAL_HTML_HREF'
    assert source['http_status'] == 200
    with zipfile.ZipFile(ROOT / source['raw_path']) as archive:
        for member in r['nse_consolidated']['members']:
            raw = archive.read(member['filename'])
            assert hashlib.sha256(raw).hexdigest() == member['sha256']
            assert len(raw) == member['bytes']


def test_layout_claims_match_actual_official_workbook_cells():
    """Schema evidence is literal; it must not silently become an expiry/cutoff."""
    r = report()['nse_consolidated']
    with zipfile.ZipFile(ROOT / r['source']['raw_path']) as archive:
        raw = archive.read(r['contract_layout']['member'])
    assert hashlib.sha256(raw).hexdigest() == r['contract_layout']['sha256']
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(raw)) as book:
        strings = [''.join(t.itertext()) for t in ET.fromstring(book.read('xl/sharedStrings.xml'))]
        relations = {x.get('Id'): x.get('Target') for x in ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))}
        sheets = ET.fromstring(book.read('xl/workbook.xml')).find('m:sheets', ns)
        assert sheets is not None
        for sheet in sheets:
            name = sheet.get('name')
            if name not in r['contract_layout']['cells']:
                continue
            key = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            target = relations[key]
            assert target is not None
            cells = {}
            for cell in ET.fromstring(book.read('xl/' + target)).findall('.//m:c', ns):
                value = cell.find('m:v', ns)
                if value is not None:
                    assert value.text is not None
                    cells[cell.get('r')] = strings[int(value.text)] if cell.get('t') == 's' else value.text
            for key, value in r['contract_layout']['cells'][name].items():
                assert cells[key] == value
    assert r['contract_layout']['cells']['Annexure 13']['D14'] == 'XpryDt'
    assert r['contract_layout']['cells']['Annexure 13']['C14'] == 'CHAR(12)'


def test_holiday_and_session_claims_are_page_bound_not_generated_expiries():
    """A published holiday/rule is not a complete production expiry calendar."""
    r = report()
    nse = r['nse_consolidated']
    raw = (ROOT / nse['text_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == nse['text_sha256']
    pages = raw.decode().split('\f')
    for claim in nse['pdf_claims']:
        assert claim['excerpt'] == pages[claim['page_number'] - 1]
    for holiday in nse['holidays']:
        assert holiday['source_line'] in pages[holiday['page_number'] - 1]
        assert holiday['published_date'] in holiday['source_line']
    assert len(nse['holidays']) == 19
    assert r['status'] == 'INDEPENDENT_REVIEW_REQUIRED'
    assert r['new_validated_production_expiries'] == 0
    assert r['production_calendar'] == 'NOT_INSTALLED'
    assert r['auto_next'] == 'FAIL-CLOSED'


def test_bankex_artifact_replays_exact_master_without_sensex_substitution():
    """Persisted observations equal the actual BANKEX rows and retain blockers."""
    bse = report()['bse_bankex']
    raw = (ROOT / bse['observations_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == bse['observations_sha256']
    records = json.loads(gzip.decompress(raw))
    assert records == observe_bse_master(*inputs(), member=MEMBER, underlying='BANKEX')
    assert len(records['contracts']) == bse['contract_count']
    assert records['observed_expiries'] == bse['observed_expiries']
    assert records['validated_production_expiries'] == []
    assert all(not row['production_eligible'] for row in records['contracts'])
