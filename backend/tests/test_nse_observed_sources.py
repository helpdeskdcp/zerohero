"""Real retained NSE metadata must not turn split fields into invented provenance."""
import json
import hashlib
from datetime import date
from pathlib import Path

import pytest

from calendar_evidence_acquisition.nse_discovery import PAGE, Failure, discover, json_records

SUMMARY = Path(__file__).resolve().parents[1] / 'calendar_evidence_acquisition/parsed/nse_page_discovery_20261003.json'


@pytest.mark.parametrize('group', ['CurrentDay', 'PreviousDay'])
def test_observed_nse_split_path_and_filename_are_not_complete_download_evidence(group):
    """Actual API metadata cannot satisfy exact-URL discovery without a captured URL."""
    evidence = json.loads(SUMMARY.read_text())
    row = next(item['observed_record'] for item in evidence['udiff_records'] if item['group']==group)
    assert row['filePath'] and row['fileActlName'] and row['tradingDate']
    records = json_records({group: [row]}, 'https://www.nseindia.com/api/daily-reports?key=FO')
    assert records == []
    with pytest.raises(Failure, match='SOURCE_METADATA_REQUIRED'):
        discover(dict(page_url=PAGE,page_status=200,page_body=b'observed metadata alone',
                      observed_at='2026-10-03T03:04:43.623968Z',records=records),date(2026,10,1))


def test_fragment_only_lifecycle_link_cannot_satisfy_provenance():
    """A published heading with href=# does not establish a lifecycle data source."""
    evidence=json.loads(SUMMARY.read_text())
    assert evidence['lifecycle']['href']=='#'
    with pytest.raises(Failure,match='SOURCE_METADATA_REQUIRED'):
        discover(dict(page_url=PAGE,page_status=200,page_body=b'heading only',
                      observed_at='2026-10-03T03:03:13.168107Z',records=[dict(
                          report_name='F&O - UDiFF Common Bhavcopy Final (zip)',
                          trading_date='2026-10-01',download_url='#',method='DOM_LINK')]),date(2026,10,1))


def test_retained_historical_response_has_explicit_contract_identity_and_original_hash():
    """Validate literal observed fields; do not derive expiry from filenames or OHLC."""
    evidence=json.loads(SUMMARY.read_text())
    history=evidence['historical_contract']
    source=next(s for s in evidence['sources'] if s['source_url']==history['data_source_url'])
    root=Path(__file__).resolve().parents[2]
    raw=(root/source['staging_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==history['data_sha256']
    rows=json.loads(raw)['data']
    assert len(rows)==history['row_count']==70
    for row in rows:
        assert (row['FH_SYMBOL'],row['FH_INSTRUMENT'],row['FH_OPTION_TYPE'])==('NIFTY','OPTIDX','CE')
        assert row['FH_TIMESTAMP']=='01-Oct-2026'
        assert row['FH_EXPIRY_DT']=='06-Oct-2026'
        assert {'FH_OPEN_INT','FH_CHANGE_IN_OI','FH_UNDERLYING_VALUE','FH_LAST_TRADED_PRICE'}<=row.keys()


def test_official_historical_observation_does_not_self_verify_calendar_or_provider():
    """Authentic exchange observations still lack complete provider/calendar evidence."""
    evidence=json.loads(SUMMARY.read_text())
    source=next(s for s in evidence['sources'] if s['source_url']==evidence['historical_contract']['data_source_url'])
    sidecar=json.loads((Path(__file__).resolve().parents[2]/source['sidecar_path']).read_text())
    assert sidecar['independently_observed'] is True
    assert sidecar['evidence_class']=='HISTORICAL_CONTRACT_DATA'
    assert sidecar['calendar_verification_status']=='UNVERIFIED'
    assert sidecar['provider_listing_verification_status']=='UNVERIFIED'
    assert sidecar['ingested'] is False
    assert evidence['production_calendar']=='NOT INSTALLED'
