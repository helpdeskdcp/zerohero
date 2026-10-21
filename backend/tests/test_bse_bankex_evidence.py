"""BANKEX observations use its own literal rows, never SENSEX dates or cutoffs."""
import pytest
from calendar_evidence_acquisition.bse_master import parse_master_csv, observe_bse_master
from test_bse_master_evidence import inputs, toy, MEMBER


def test_bankex_retained_official_master_has_three_literal_expiries():
    result = observe_bse_master(*inputs(), member=MEMBER, underlying='BANKEX')
    assert len(result['contracts']) == 1049
    assert result['observed_expiries'] == ['2026-10-29', '2026-11-26', '2026-12-31']
    assert {r['instrument'] for r in result['contracts']} == {'BANKEX'}
    assert {r['cycle'] for r in result['contracts']} == {'MONTHLY'}
    assert result['duplicates'] == result['conflicts'] == []
    assert all(r['classification'] == 'OBSERVED_OFFICIAL_EXPIRY' for r in result['contracts'])
    assert all(r['last_trading_at'] is None and r['listed_at'] is None and not r['production_eligible'] for r in result['contracts'])
    assert result['validated_production_expiries'] == []
    assert result['production_calendar'] == 'NOT_INSTALLED' and result['auto_next'] == 'FAIL-CLOSED'


def test_symbol_filter_does_not_cross_contaminate_bse_indices():
    raw = toy(TckrSymb='BANKEX')
    assert parse_master_csv(raw)['contracts'] == []
    result = parse_master_csv(raw, underlying='BANKEX')
    assert len(result['contracts']) == 1 and result['contracts'][0]['instrument'] == 'BANKEX'
    assert parse_master_csv(toy(), underlying='BANKEX')['contracts'] == []


@pytest.mark.parametrize('symbol', ['NIFTY', '', 'UNKNOWN'])
def test_unsupported_underlying_is_rejected_before_parsing(symbol):
    with pytest.raises(ValueError, match='BSE_MASTER_UNDERLYING_UNSUPPORTED'):
        parse_master_csv(b'not a CSV', underlying=symbol)


def test_empty_bankex_master_does_not_claim_complete_coverage():
    result = parse_master_csv(toy(), underlying='BANKEX')
    assert result['contracts'] == [] and not result['complete_historical_calendar']
    assert result['status'] == 'UNVERIFIED'
