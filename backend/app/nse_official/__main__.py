"""Opt-in historical ingestion; default validation uses a temporary evidence store."""
import argparse
import json
from pathlib import Path
import tempfile

from .adapter import NSEAdapter
from .core import DataError
from .provenance import SourceRegistry
from .store import NSEStore


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--inventory', type=Path, required=True)
    for name in ('symbol', 'instrument-type', 'trade-date', 'expiry', 'option-type'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--expected-row-count', type=int)
    parser.add_argument('--ingest', action='store_true')
    parser.add_argument('--production', action='store_true', help='Blocked pending reviewed gates and explicit activation')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--live', action='store_true', help='Opt in to policy-checked GET of the exact registered URL')
    args = parser.parse_args(argv)
    if args.production:
        from .controls import production_gate
        print(json.dumps(production_gate(),sort_keys=True))
        return 2
    if args.ingest and args.output is None:
        parser.error('--ingest requires an explicit --output store')
    if args.output is not None and not args.ingest:
        parser.error('--output requires --ingest; default validation writes only temporary files')
    with tempfile.TemporaryDirectory(prefix='nse-official-validation-') as temporary:
        try:
            registry = SourceRegistry.from_inventory(args.inventory, args.repository)
            store = NSEStore(args.output if args.ingest else Path(temporary))
            transport = None
            if args.live:
                from .transport import OfficialHTTPTransport
                transport = OfficialHTTPTransport(registry, store)
            adapter = NSEAdapter(registry, store, transport=transport)
            result = adapter.fetch_nse_historical_contract_data(
                symbol=args.symbol, instrument_type=args.instrument_type, trade_date=args.trade_date,
                expiry=args.expiry, option_type=args.option_type, expected_row_count=args.expected_row_count)
            summary = {k: result[k] for k in ('source', 'status', 'reason', 'endpoint', 'response_status',
                       'sha256', 'row_count', 'data_timestamp', 'fetch_timestamp', 'processing_timestamp',
                       'freshness_status', 'calendar_status', 'auto_next')}
            summary['mode'] = 'LIVE_HTTP' if args.live else 'RETAINED_OBSERVATION'
            if args.ingest:
                summary['ingestion'] = store.ingest(result)
            else:
                summary['ingestion'] = {'status': 'NOT_REQUESTED'}
            print(json.dumps(summary, sort_keys=True))
            return 0 if result['status'] == 'OK' and summary['ingestion']['status'] in (
                'INGESTED', 'ALREADY_INGESTED', 'NOT_REQUESTED') else 1
        except (DataError, OSError, KeyError, TypeError, ValueError):
            print(json.dumps({'status': 'SOURCE_METADATA_REQUIRED', 'reason': 'REGISTRY_OR_STORE_INVALID',
                              'calendar_status': 'NOT INSTALLED', 'auto_next': 'FAIL-CLOSED'}))
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
