"""Natural Gas-only release: excluded contracts cannot block or become active."""
import hashlib
import json
from pathlib import Path

import pytest

from app.nse_official.own_exchange import OwnExchangeProvider

ROOT = Path(__file__).resolve().parents[2]
SCOPE = ROOT / 'backend/calendar_evidence_acquisition/production_scope.json'


def scope():
    return json.loads(SCOPE.read_text())


def test_no_mcx_futures_are_required_or_pending_after_owner_scope_change():
    s = scope()
    assert s['required_calendars'] == []
    assert s['production_requirement_failures'] == []
    assert s['pending_requirements'] == []
    assert s['status'] == 'DISABLED' and s['scope'] == 'OUT_OF_SCOPE'
    assert s['production'] == 'NOT_ENABLED'


@pytest.mark.parametrize('instrument', ['NATURALGAS', 'NATGASMINI', 'CRUDEOIL', 'CRUDEOILM', 'GOLD', 'GOLDMINI', 'SILVER', 'SILVERMINI'])
def test_excluded_instruments_have_no_pending_acquisition_or_installation_requirement(instrument):
    s = scope()
    record = next(x for x in s['excluded_instruments'] if x['instrument'] == instrument)
    assert record['status'] == 'DISABLED' and record['scope'] == 'OUT_OF_SCOPE'
    assert not record['acquisition_required'] and not record['blocks_installation']
    assert record['pending_requirements'] == []
    assert not any(r['instrument'] in (instrument, record['canonical_instrument']) for r in s['required_calendars'])


def test_scope_readback_cannot_claim_installation_or_activation_from_published_dates():
    s = scope()
    assert s['active_calendars'] == s['auto_next_enabled_calendars'] == []
    assert s['installation_path'] is None
    assert s['production_calendar'] == 'NOT_INSTALLED' and s['auto_next'] == 'DISABLED'
    evidence = s['normalized_schedule_evidence']
    raw = (ROOT / evidence['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == evidence['sha256']
    b = json.loads(raw)
    assert len(b['published_schedule_entries']) == 36
    assert b['validated_production_expiries'] == []
    assert all(r['last_trading_at'] is None for r in b['published_schedule_entries'])
    assert all(d['retrieval_timestamp'] is None for d in b['documents'])
    assert all(d['missing_claims']['holiday_adjustment_rule'] == 'NOT_STATED' for d in b['documents'])
    for d in b['documents']:
        assert hashlib.sha256((ROOT / d['raw_path']).read_bytes()).hexdigest() == d['sha256']


@pytest.mark.parametrize('instrument', ['NATURALGAS', 'NATGASMINI', 'CRUDEOIL', 'CRUDEOILM', 'CRUDEOILMINI'])
@pytest.mark.parametrize('want', ['AUTO', 'NEXT'])
def test_no_calendar_is_implicitly_loaded_or_enabled(want, instrument):
    result = OwnExchangeProvider('MCX').preview(want, instrument, '2026-10-05T12:00:00+05:30', instrument='FUTURE')
    assert result['status'] == 'DATA_UNAVAILABLE' and result['expiry'] is None
    assert result['production_calendar'] == 'NOT_INSTALLED'
    assert result['auto_next'] == 'DISABLED'
