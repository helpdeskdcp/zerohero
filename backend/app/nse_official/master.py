"""Review-only master and expiry preview from exact observed responses.

A selector is not a contract list. Never cross-product its strikes and expiries.
This module cannot install calendars or claim a complete exchange universe.
"""
from .core import DataError, day

SYMBOLS = ('NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY')


def build_preview(adapter) -> dict:
    contracts, expiries, failures = [], [], []
    seen: dict = {}
    for observation in adapter.registry.observations:
        p = observation.parameters
        if p.get('symbol') not in SYMBOLS:
            continue
        if observation.kind == 'historical':
            result = adapter.fetch_nse_historical_contract_data(
                symbol=p['symbol'], instrument_type=p['instrumentType'], trade_date=day(p['from']),
                expiry=day(p['expiryDate']), option_type=p['optionType'])
            if result['status'] != 'OK':
                failures.append(dict(endpoint=observation.endpoint, reason=result['reason']))
                continue
            for item in result['contracts']:
                row = dict(item, lot_size=item['lot_size_if_available'],
                           contract_identifier=item.get('identifier'), token=item['token_if_available'],
                           observed_at=result['received_timestamp'], missing_fields={},
                           provenance=dict(source_url=item['source_url'], source_hash=item['source_hash'],
                                           acquisition=result['provenance']))
                for field in ('lot_size', 'contract_identifier', 'token', 'source_timestamp'):
                    if row[field] is None:
                        row['missing_fields'][field] = 'NOT_PUBLISHED_IN_RESPONSE'
                identity = tuple(row[k] for k in ('exchange','symbol','instrument_type','expiry','strike','option_type','valid_on_trade_date'))
                if identity in seen:
                    if any(seen[identity][k] != row[k] for k in ('lot_size','contract_identifier','token')):
                        raise DataError('CONFLICTING_CONTRACT_EVIDENCE')
                    continue
                seen[identity] = row
                contracts.append(row)
        elif observation.kind == 'live_selector':
            result = adapter.fetch_nse_expiry_selector(symbol=p['symbol'], instrument_type='OPTIDX')
            if result['status'] != 'OK':
                failures.append(dict(endpoint=observation.endpoint, reason=result['reason']))
                continue
            for item in result['rows']:
                expiries.append(dict(item, source_timestamp=None,
                                     source_timestamp_reason='NOT_PUBLISHED_IN_RESPONSE',
                                     validation_status='OBSERVED_EXPIRY_NOT_CALENDAR_VERIFIED'))
    mapped = {(r['symbol'], r['instrument_type'], r['expiry']) for r in contracts}
    for row in expiries:
        row['observed_contract_mapping'] = (row['symbol'],row['instrument'],row['expiry']) in mapped
    return dict(status='PARTIAL', contract_universe_complete=False, contracts=contracts,
                expiry_preview=expiries, rejected_responses=failures,
                coverage={s:sum(r['symbol']==s for r in contracts) for s in SYMBOLS},
                unsupported_exchange_sources={'SENSEX':'BSE_OFFICIAL_SOURCE_REQUIRED','MCX':'MCX_OFFICIAL_SOURCE_REQUIRED'},
                blockers=['COMPLETE_CONTRACT_MASTER_NOT_OBSERVED','LISTING_LIFECYCLE_AND_CUTOFF_EVIDENCE_REQUIRED',
                          'INDEPENDENT_CALENDAR_REVIEW_REQUIRED'],
                production_calendar='NOT_INSTALLED', auto_next='FAIL-CLOSED')


def main(argv=None) -> int:
    """Print a preview; all normalization receipts use a temporary store."""
    import argparse
    import json
    from pathlib import Path
    import tempfile
    from .adapter import NSEAdapter
    from .provenance import SourceRegistry
    from .store import NSEStore
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--inventory', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        registry = SourceRegistry.from_inventory(args.inventory, args.repository)
        with tempfile.TemporaryDirectory(prefix='nse-master-preview-') as tmp:
            print(json.dumps(build_preview(NSEAdapter(registry, NSEStore(Path(tmp)))), indent=2))
        return 0
    except (DataError, OSError, KeyError, TypeError, ValueError):
        print(json.dumps(dict(status='SOURCE_METADATA_REQUIRED', reason='PREVIEW_VALIDATION_FAILED',
                              production_calendar='NOT_INSTALLED', auto_next='FAIL-CLOSED')))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
