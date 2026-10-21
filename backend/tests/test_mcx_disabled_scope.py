"""Owner-disabled MCX cannot acquire, generate, resolve or block calendar readiness."""
from datetime import date
from pathlib import Path
import json

import pytest

from app.hist_options.provider_calendar import EXCHANGES, ProviderCalendars, resolve
from app.nse_official.own_exchange import OwnExchangeProvider
from calendar_evidence_acquisition.evidence_engine import EvidenceEngine, playwright_download
from calendar_evidence_acquisition.static_archives import SourceCache, SourceSpec


@pytest.mark.parametrize('symbol', [s for s, ex in EXCHANGES.items() if ex == 'MCX'] + ['NATGAS', 'NG', 'CRUDEOILM', 'NGMINI'])
@pytest.mark.parametrize('want', ['AUTO', 'NEXT', '2026-10-27'])
def test_disabled_exchange_blocks_every_instrument_and_alias_even_explicit_dates(symbol, want):
    with pytest.raises(ValueError, match='CALENDAR_EXCHANGE_DISABLED'):
        resolve(want, 'DHAN', symbol, '2026-10-05T12:00:00+05:30', ProviderCalendars())
    r = OwnExchangeProvider('MCX').preview(want, symbol, '2026-10-05T12:00:00+05:30', instrument='FUTURE')
    assert r['expiry'] is None and r['reason'] == 'CALENDAR_EXCHANGE_DISABLED'
    assert r['auto_next'] == 'DISABLED' and r['scope'] == 'OUT_OF_SCOPE'


def test_disabled_exchange_does_not_require_any_production_gate_evidence():
    from app.nse_official.controls import production_gate
    for ex in ['MCX', 'mcx']:
        r = production_gate(exchange=ex)
        assert r['PRODUCTION'] == 'NOT_ENABLED' and r['AUTO/NEXT'] == 'DISABLED'
        assert r['missing_gates'] == [] and not r['eligible_for_review']
        assert not r['gate_required'] and not r['calendar_installed']
    assert production_gate()['missing_gates']  # Existing NSE requirements remain.
    assert production_gate(exchange='BSE')['missing_gates']


def test_disabled_generation_never_uses_a_supplied_calendar_or_broker_list():
    from app.hist_options.expiry import Calendar, mcx_option_expiry, from_dhan_expiry_list
    cal = Calendar('MCX', [date(2026, 10, d) for d in range(1, 29)])
    for r in [mcx_option_expiry('2026-10-27', cal, underlying='NATURALGAS'),
              from_dhan_expiry_list('GOLD', '2026-10-05', ['2026-10-27'])]:
        assert r.expiry_date is None and r.reason == 'CALENDAR_EXCHANGE_DISABLED'


def test_acquisition_blocked_before_fetch_browser_or_filesystem_writes(tmp_path):
    calls = []
    def fetch(url):
        calls.append(url)
        return 200, b'not evidence', ''
    with pytest.raises(ValueError, match='CALENDAR_EXCHANGE_DISABLED'):
        SourceCache(tmp_path/'cache').acquire(SourceSpec('https://www.mcxindia.com/test', 'OFFICIAL_ARCHIVE', 'MCX', 'TEST'), fetch)
    with pytest.raises(ValueError, match='CALENDAR_EXCHANGE_DISABLED'):
        playwright_download(exchange='MCX', trading_date=date(2026, 10, 1), report_page='https://www.mcxindia.com/')
    with pytest.raises(ValueError, match='CALENDAR_EXCHANGE_DISABLED'):
        EvidenceEngine(tmp_path/'engine').browser('MCX', date(2026, 10, 1), 'https://www.mcxindia.com/', adapter=fetch)
    assert calls == [] and list(tmp_path.iterdir()) == []


def test_new_ingestion_is_blocked_before_raw_retention_or_ledger_write(tmp_path):
    engine = EvidenceEngine(tmp_path/'engine')
    with pytest.raises(ValueError, match='CALENDAR_EXCHANGE_DISABLED'):
        engine._ingest(b'retained but not publishable', 'x.csv', {'exchange':'MCX'}, 'MANUAL')
    assert not engine.root.exists()


def test_no_mcx_acquisition_requirements_or_coverage_blockers(tmp_path):
    from calendar_evidence_acquisition.official_eod import required_windows
    assert all(ex != 'MCX' for ex, _, _ in required_windows().values())
    result = EvidenceEngine(tmp_path).status()
    mcx = [r for r in result['combinations'] if r['exchange'] == 'MCX']
    assert len(mcx) == 16
    assert all(r['status'] == 'DISABLED' and r['blockers'] == [] and r['required_range'] is None for r in mcx)
    assert all(r['pending_requirements'] == [] and r['auto_next'] == 'DISABLED' for r in mcx)
    assert all(r['status'] == 'UNVERIFIED' for r in result['combinations'] if r['exchange'] != 'MCX')


def test_explicit_nse_bse_dates_stay_authoritative():
    for symbol in ['NIFTY', 'SENSEX']:
        expiry, p = resolve('2026-10-27', 'DHAN', symbol, '2026-10-05T00:00:00Z')
        assert expiry.isoformat() == '2026-10-27'
        assert p['verification_status'] == 'EXPLICIT_NOT_CALENDAR_VERIFIED'


def test_reviewed_evidence_cannot_override_disabled_exchange():
    from test_official_expiry_calendar import evidence
    from calendar_fixtures import synthetic_evidence
    for reviewed in [evidence('NATURALGAS', 'MCX'), synthetic_evidence(underlying='NATURALGAS')]:
        with pytest.raises(ValueError, match='CALENDAR_EXCHANGE_DISABLED'):
            reviewed.validated()


def test_retained_mcx_ledger_cannot_block_active_coverage_when_files_are_missing(tmp_path):
    engine = EvidenceEngine(tmp_path)
    raw = json.dumps({'schema_version':'exchange-evidence-ledger/v1', 'entries':[
        {'exchange':'MCX', 'raw_path':'absent.bin'}]}).encode()
    engine.ledger.write_bytes(raw)
    status = engine.status()
    assert status['counts']['DISABLED'] == 16
    assert status['counts']['UNVERIFIED'] == 12
    assert engine.ledger.read_bytes() == raw


def test_mcx_checks_do_not_block_complete_nse_bse_review(tmp_path):
    from app.nse_official.controls import production_gate, GATES
    from app.nse_official.core import sha
    report = tmp_path/'review.json'
    raw = json.dumps(dict(status='PASS',complete=True,unexpected_failures=0,errors=0,timeouts=0)).encode()
    report.write_bytes(raw)
    checks = {key:dict(evidence_path=str(report),sha256=sha(raw),independently_reviewed=True) for key in GATES}
    checks['MCX'] = {'status':'MISSING_EVIDENCE'}
    for exchange in ['NSE','BSE']:
        gate = production_gate(checks, exchange=exchange)
        assert gate['eligible_for_review'] and gate['missing_gates'] == []
        assert not gate['calendar_installed']  # Own evidence gates and manual activation stay intact.


def test_excluded_staged_file_does_not_fail_active_ingestion_batch(tmp_path, capsys):
    from test_evidence_engine import staged
    from calendar_evidence_acquisition.evidence_engine import main
    engine, file = staged(tmp_path, exchange='MCX', provenance_url='https://www.mcxindia.com/test.csv')
    original = file.read_bytes()
    assert main(['--staging', str(engine.staging), '--ingest']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'DISABLED' and result['scope'] == 'OUT_OF_SCOPE'
    assert file.read_bytes() == original and not engine.ledger.exists()
