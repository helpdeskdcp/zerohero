"""Read-only acquisition coverage, deliberately separate from calendar approval.

This ledger contains unreviewed exchange observations, not provider listing proofs.
No ingestion-side metadata can grant VERIFIED or PARTIALLY_VERIFIED. Those states
require the existing independently reviewed provider-calendar evidence workflow.
"""
from typing import TYPE_CHECKING

from .official_eod import required_windows, reconcile

if TYPE_CHECKING:
    from app.hist_options.calendar_scope import require_calendar_exchange, calendar_disabled
elif __package__.startswith("backend."):
    from ..app.hist_options.calendar_scope import require_calendar_exchange, calendar_disabled
else:
    from app.hist_options.calendar_scope import require_calendar_exchange, calendar_disabled

STATES = ('VERIFIED', 'PARTIALLY_VERIFIED', 'UNVERIFIED', 'CONFLICTING', 'NOT_APPLICABLE', 'DISABLED')


def coverage_status(inventories: list[tuple[dict, dict]], exchanges: dict) -> dict:
    inventories = [(entry, inv) for entry, inv in inventories
                   if not calendar_disabled(entry.get("exchange", ""))]
    windows = required_windows()
    conflicts = reconcile([inventory for _, inventory in inventories])['conflicts']
    affected = {(conflict['contract_key'][0], claim['values'][0])
                for conflict in conflicts for claim in conflict['claims']}
    combinations = []
    for symbol, exchange in exchanges.items():
        if calendar_disabled(exchange):
            for provider in ('DHAN', 'ANGELONE'):
                combinations.append(dict(provider=provider, exchange=exchange, instrument=symbol,
                    status='DISABLED', scope='OUT_OF_SCOPE', production='NOT_ENABLED',
                    auto_next='DISABLED', pending_requirements=[], blockers=[], required_range=None,
                    candidate_expiry_dates=[], official_observed_expiry_dates=[],
                    qualifying_source_count=0, missing_required_date_ranges=[],
                    evidence_disposition='HISTORICAL_RETAINED_ONLY'))
            continue
        window = windows.get(symbol)
        required = dict(start=window[1], end=window[2]) if window else None
        sources: dict = {}
        observations = []
        for entry, inventory in inventories:
            if entry['exchange'] != exchange:
                continue
            rows = [r for r in inventory.get('selected_observations', [])
                    if r['underlying'] == symbol and
                    (window is None or window[1] <= r['trading_date'] <= window[2])]
            if not rows:
                continue
            # Same bytes through two routes or repeated metadata are one source.
            sources.setdefault(entry['sha256'], dict(sha256=entry['sha256'],
                trading_date=entry['trading_date'], provenance_url=entry['provenance_url'],
                publisher_status=entry['publisher_status'], original_filename=entry['original_filename']))
            observations.extend(rows)
        days = sorted({r['trading_date'] for r in observations})
        blockers = ['INDEPENDENT_OFFICIAL_SOURCE_REVIEW_REQUIRED',
                    'COMPLETE_LISTED_UNIVERSE_AND_PUBLICATION_CUTOFF_EVIDENCE_REQUIRED',
                    'OFFICIAL_TRADING_SESSION_CALENDAR_REQUIRED',
                    'PROVIDER_HISTORICAL_LISTING_EVIDENCE_MISSING']
        if required is None:
            blockers.insert(0, 'REQUIRED_SCOPE_UNESTABLISHED')
        if not days:
            blockers.insert(0, 'NO_PARSED_OBSERVATIONS_IN_REQUIRED_SCOPE')
        state = 'CONFLICTING' if (exchange, symbol) in affected else 'UNVERIFIED'
        if state == 'CONFLICTING':
            blockers.insert(0, 'CONFLICTING_CONTRACT_OBSERVATIONS')
        for provider in ('DHAN', 'ANGELONE'):
            combinations.append(dict(provider=provider, exchange=exchange, instrument=symbol,
                status=state, required_range=required,
                candidate_expiry_dates=sorted({r['expiry_date'] for r in observations}),
                observed_contract_types=sorted({r['instrument'] for r in observations}),
                official_observed_expiry_dates=[], first_observed_trading_date=days[0] if days else None,
                last_observed_trading_date=days[-1] if days else None,
                observed_trading_dates=days, candidate_source_count=len(sources),
                qualifying_source_count=0, sha256_evidence=sorted(sources), provenance=list(sources.values()),
                missing_required_date_ranges=[dict(required, reason='OFFICIAL_COVERAGE_NOT_INDEPENDENTLY_VERIFIED')] if required else [],
                missing_trading_dates=None,
                missing_trading_dates_reason='TRADING_SESSION_CALENDAR_UNAVAILABLE_NO_WEEKDAY_INFERENCE',
                provider_listing_status='UNVERIFIED', blockers=list(blockers)))
    return dict(schema_version='staging-coverage/v1', combinations=combinations,
                counts={state: sum(r['status'] == state for r in combinations) for state in STATES},
                production_calendar='NOT INSTALLED', auto_next='FAIL-CLOSED',
                scope='Acquisition ledger only; this command neither loads nor activates production calendars',
                activation_eligible=False,
                verification_policy='Existing reviewed provider-calendar/v1 policy; staging cannot self-verify')
