"""Retained MCX schedules are literal document evidence, not production calendars."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / 'backend/calendar_evidence_acquisition/parsed/mcx_naturalgas_local_20261004/evidence.json'


def evidence():
    return json.loads(BUNDLE.read_text())


# Transcribed from the three published tables, not generated from weekday rules.
EXPECTED = {
    2025: ['01-28', '02-25', '03-26', '04-25', '05-27', '06-25',
           '07-28', '08-26', '09-25', '10-28', '11-24', '12-26'],
    2026: ['01-27', '02-24', '03-26', '04-27', '05-26', '06-25',
           '07-28', '08-26', '09-25', '10-27', '11-24', '12-28'],
    2027: ['01-25', '02-23', '03-25', '04-27', '05-25', '06-25',
           '07-27', '08-26', '09-27', '10-26', '11-24', '12-28'],
}
HASHES = {
    2025: '4101b025fe36cb595b7843236b50e9495fd60175b0348c39e565f7d9b08f6441',
    2026: '2f15e2ecc7f610686307d667b94c0788659b8a58acc539ec8bef96e7711cb74a',
    2027: '4a31495a37e671df9910a8bfbeed3b06a2da752730628dfa8a7d9d6b61b55e6e',
}


@pytest.mark.parametrize('year', [2025, 2026, 2027])
def test_original_pdf_and_extracted_page_hashes_are_bound_to_operator_provenance(year):
    doc = next(d for d in evidence()['documents'] if d['calendar_year_explicit'] == year)
    raw = (ROOT / doc['raw_path']).read_bytes()
    assert raw.startswith(b'%PDF-')
    assert hashlib.sha256(raw).hexdigest() == doc['sha256'] == HASHES[year]
    assert urlsplit(doc['source_url']).hostname == 'www.mcxindia.com'
    assert doc['source_url'].endswith('/' + doc['source_document'])
    assert doc['acquisition_method'] == 'OPERATOR_PROVIDED_ARTIFACT'
    assert doc['independently_downloaded'] is False
    assert doc['retrieval_timestamp'] is None and doc['http_status'] is None
    assert datetime.fromisoformat(doc['local_inspected_at']).tzinfo is not None
    for path_key, hash_key in [('text_path', 'text_sha256'), ('pdfinfo_path', 'pdfinfo_sha256')]:
        assert hashlib.sha256((ROOT / doc[path_key]).read_bytes()).hexdigest() == doc[hash_key]
    pages = [p for p in (ROOT / doc['text_path']).read_text().split('\f') if p.strip()]
    assert len(pages) == doc['pdf_pages'] == (3 if year == 2025 else 2)
    for claim in doc['claims']:
        assert claim['quote'] in ' '.join(pages[claim['page_number'] - 1].split())
    assert doc['reference_circular_independently_acquired'] is False


@pytest.mark.parametrize('year,month,expected', [
    (year, month, f'{year}-{day}')
    for year, dates in EXPECTED.items() for month, day in enumerate(dates, 1)
])
def test_each_date_is_literal_on_cited_calendar_page_not_an_inferred_expiry(year, month, expected):
    b = evidence()
    row = next(r for r in b['published_schedule_entries'] if r['contract_month'] == f'{year}-{month:02d}')
    doc = next(d for d in b['documents'] if d['calendar_year_explicit'] == year)
    assert row['expiry_date'] == expected
    assert row['page_number'] == doc['calendar_page'] == (3 if year == 2025 else 2)
    page = (ROOT / doc['text_path']).read_text().split('\f')[row['page_number'] - 1]
    assert row['raw_row'] in page
    cells = re.split(r'\s{2,}', row['raw_row'])
    assert cells == [row['contract_month_literal'], row['launch_date_literal'], row['expiry_date_literal']]
    fmt = {2025: '%d-%b-%y', 2026: '%B %d, %Y', 2027: '%d %B %Y'}[year]
    assert datetime.strptime(cells[1], fmt).date().isoformat() == row['launch_date']
    assert datetime.strptime(cells[2], fmt).date().isoformat() == expected
    assert row['launch_date'] < row['expiry_date']
    assert row['source_hash'] == doc['sha256'] and row['source_url'] == doc['source_url']
    assert row['reference_circular'] in page
    assert row['last_trading_date'] == expected and row['last_trading_date_claim_page'] == 1
    assert row['last_trading_at'] is None and row['maturity_date'] is None
    assert row['evidence_class'] == 'OFFICIAL_PUBLISHED_CONTRACT_SCHEDULE'
    assert row['historical_trading_observed'] is False and row['production_eligible'] is False


def test_schedule_scope_does_not_claim_historical_trades_provider_listings_or_other_instruments():
    b = evidence()
    assert b['published_schedule_count'] == len(b['published_schedule_entries']) == 36
    assert len({r['contract_month'] for r in b['published_schedule_entries']}) == 36
    assert {(r['exchange'], r['instrument'], r['contract_type']) for r in b['published_schedule_entries']} == {('MCX', 'NATURALGAS', 'FUTURE')}
    assert b['historical_trading_observations'] == b['validated_production_expiries'] == []
    assert b['duplicates'] == b['conflicts'] == []
    assert b['production_calendar'] == 'NOT_INSTALLED' and b['auto_next'] == 'FAIL-CLOSED'
    assert b['review_status'] == 'INDEPENDENT_REVIEW_REQUIRED'
    assert b['network_requests'] == 0
    for doc in b['documents']:
        assert doc['missing_claims']['holiday_adjustment_rule'] == 'NOT_STATED'
        assert doc['missing_claims']['dated_expiry_cutoff'] == 'NOT_ESTABLISHED'
        assert doc['missing_claims']['amendment_completeness'] == 'NOT_ESTABLISHED'


def test_previous_http_denials_remain_separate_from_document_bytes():
    old = json.loads((ROOT / 'backend/calendar_evidence_acquisition/parsed/mcx_specification_candidates_20261004.json').read_text())
    for failed in old['sources']:
        raw = (ROOT / failed['raw_path']).read_bytes()
        assert failed['http_status'] == 403 and not raw.startswith(b'%PDF-')
        assert failed['sha256'] not in HASHES.values()
        assert failed['observed_expiries'] == []
