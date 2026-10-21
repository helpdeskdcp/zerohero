"""BSE/MCX evidence adapters over the shared official-calendar implementation.

Offline trusted-curator API, not an ingestion endpoint. No default schemas,
network calls, rule-derived dates or production installation. Explicit field
maps must be reviewed against the retained exchange file before use. Observed
contracts are not evidence of complete listings, lifecycle or holiday rules.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import ClassVar

from calendar_evidence_acquisition.official_eod import FieldMap, Report, parse_eod
from ..hist_options.provider_calendar import exact_date, identity, instant, IST
from .calendar import OfficialCalendarEvidence, OfficialCalendars, OfficialSource

from ..hist_options.calendar_scope import calendar_disabled, disabled_calendar_result

OBSERVED = 'OBSERVED_OFFICIAL_EXPIRY'
VALIDATED = 'VALIDATED_PRODUCTION_EXPIRY'
MISSING = 'MISSING_EVIDENCE'
TYPES = {'BSE': {'OPTIDX': 'OPTION', 'FUTIDX': 'FUTURE'},
         'MCX': {'OPTFUT': 'OPTION', 'FUTCOM': 'FUTURE'}}


def _number(value: str, *, positive=False, integer=False):
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError('CONTRACT_NUMERIC_INVALID') from exc
    if (not number.is_finite() or number < 0 or (positive and number == 0)
            or (integer and number != number.to_integral_value())):
        raise ValueError('CONTRACT_NUMERIC_INVALID')
    # Preserve exact strikes, never introduce binary rounding.
    return int(number) if integer else str(number)


@dataclass(frozen=True)
class OwnExchangeCalendarEvidence(OfficialCalendarEvidence):
    """Own-exchange review additionally binds holiday claims; no date calculation.

    A published adjustment must cite its nominal and actual dates explicitly.
    Reviewers substantiate the holiday-calendar scope and adjustment rule; this
    code checks claim integrity, not the truth of arbitrary supplied documents.
    """
    supported_exchanges: ClassVar[frozenset] = frozenset(TYPES)

    def validated(self) -> dict:
        m = super().validated()
        if m['exchange'] not in self.supported_exchanges:
            raise ValueError('CALENDAR_OWN_EXCHANGE_REQUIRED')
        hashes = {s.sha256 for s in self.sources}

        def cited(claims, name):
            refs = claims.get(name)
            if not isinstance(refs, list) or not refs or any(r not in hashes for r in refs):
                raise ValueError('CALENDAR_HOLIDAY_EVIDENCE_REQUIRED')

        for name in ('holiday_calendar', 'holiday_adjustment_rule'):
            cited(m['calendar_claims'], name)
        for entry in m['entries']:
            if entry.get('classification') != OBSERVED:
                raise ValueError('CALENDAR_OBSERVED_EXPIRY_REQUIRED')
            if entry.get('nominal_expiry') is not None:
                nominal = exact_date(entry['nominal_expiry'])
                if nominal != exact_date(entry['expiry']):
                    cited(entry['claims'], 'holiday_adjustment')
        return m


@dataclass(frozen=True)
class OwnExchangeProvider:
    """Use exchange='BSE' or 'MCX'. Evidence must be independently reviewed."""
    exchange: str
    evidence: tuple[OwnExchangeCalendarEvidence, ...] = ()

    def __post_init__(self):
        if self.exchange not in TYPES:
            raise ValueError('CALENDAR_OWN_EXCHANGE_REQUIRED')
        if any(not isinstance(e, OwnExchangeCalendarEvidence) for e in self.evidence):
            raise ValueError('CALENDAR_OWN_EXCHANGE_REVIEW_REQUIRED')

    def normalize(self, report: Report, source: OfficialSource, fields: FieldMap,
                  *, lot_size_field: str | None = None) -> dict:
        """Normalize a whole observed CSV/archive or reject; retain raw row pointers.

        The existing parser checks discovery link, trading date, columns, duplicate
        IDs and expiry/maturity separation. This adapter never installs field maps.
        """
        source.validate(self.exchange)
        if report.exchange != self.exchange or report.source_url != source.url:
            raise ValueError('CONTRACT_EXCHANGE_SOURCE_MISMATCH')
        expected_segment = 'FO' if self.exchange == 'BSE' else 'COM'
        if report.segment != expected_segment:
            raise ValueError('CONTRACT_SEGMENT_MISMATCH')
        if report.trading_date > instant(source.retrieved_at).astimezone(IST).date():
            raise ValueError('CONTRACT_OBSERVATION_IN_FUTURE')
        if not fields.exchange or not fields.segment:
            raise ValueError('CONTRACT_EXCHANGE_FIELDS_REQUIRED')
        inv = parse_eod(source.raw, report, fields)
        if inv['verification_status'] == 'CONFLICTING':
            raise ValueError('CONTRACT_SOURCE_CONFLICT')
        if not inv['observations']:
            raise ValueError('CONTRACT_ROWS_UNAVAILABLE')
        contracts = []
        seen = set()
        identifiers: dict[str, tuple] = {}
        for row in inv['observations']:
            symbol, exchange = identity(row['underlying'])
            if exchange != self.exchange:
                raise ValueError('CONTRACT_EXCHANGE_MISMATCH')
            kind = row['instrument']
            if kind not in TYPES[self.exchange]:
                raise ValueError('CONTRACT_TYPE_UNSUPPORTED')
            contract_type = TYPES[self.exchange][kind]
            if exact_date(row['expiry_date']) < report.trading_date:
                raise ValueError('CONTRACT_EXPIRED_AT_OBSERVATION')
            strike, side = None, None
            if contract_type == 'OPTION':
                side = row['option_type']
                if side not in ('CE', 'PE') or row['strike'] in (None, ''):
                    raise ValueError('CONTRACT_OPTION_IDENTITY_REQUIRED')
                strike = _number(row['strike'])
            lot = None
            if lot_size_field:
                if lot_size_field not in row['raw_fields']:
                    raise ValueError('CONTRACT_LOT_FIELD_MISSING')
                raw_lot = row['raw_fields'][lot_size_field]
                if raw_lot.strip():
                    lot = _number(raw_lot, positive=True, integer=True)
            key = tuple(sorted(row['raw_fields'].items()))
            contract_id = row['contract_identifier']
            signature = (symbol, kind, row['expiry_date'], row['contract_maturity_date'],
                         row['actual_expiry_date'], strike, side, lot)
            if contract_id:
                if contract_id in identifiers and identifiers[contract_id] != signature:
                    raise ValueError('CONTRACT_SOURCE_CONFLICT')
                identifiers[contract_id] = signature
            if key in seen:
                continue
            seen.add(key)
            missing = dict(last_trading_at='CUTOFF_EVIDENCE_REQUIRED',
                           listed_at='LISTING_EVIDENCE_REQUIRED',
                           source_timestamp='TRADING_DATE_ONLY')
            if lot is None:
                missing['lot_size'] = 'FIELD_NOT_PUBLISHED'
            if row['contract_identifier'] is None:
                missing['contract_identifier'] = 'FIELD_NOT_PUBLISHED'
            contracts.append(dict(exchange=exchange, segment=report.segment,
                underlying=symbol, source_symbol=row['underlying'], instrument_type=kind,
                contract_type=contract_type, expiry=row['expiry_date'],
                contract_maturity_date=row['contract_maturity_date'],
                actual_expiry_date=row['actual_expiry_date'], last_trading_at=None, listed_at=None,
                strike=strike, option_type=side, lot_size=lot,
                contract_identifier=row['contract_identifier'], source=exchange+'_OFFICIAL',
                source_timestamp=None, data_date=row['trading_date'],
                retrieved_at=source.retrieved_at, classification=OBSERVED,
                unavailable_reasons=missing, provenance=dict(source_url=source.url,
                    source_hash=source.sha256, discovery_url=report.discovery.source_url,
                    discovery_sha256=report.discovery.sha256, parser_version=inv['parser_version'],
                    field_map=inv['field_map'], lot_size_field=lot_size_field,
                    row_number=row['row_number'])))
        return dict(contracts=contracts, complete_contract_master=False,
                    verification_status='UNVERIFIED', provider_listing_status='UNVERIFIED',
                    duplicate_rows=inv['duplicate_rows'], production_calendar='NOT_INSTALLED',
                    auto_next='FAIL-CLOSED')

    def preview(self, want, underlying, as_of, *, instrument='OPTION', cycle=None) -> dict:
        if calendar_disabled(self.exchange):
            return disabled_calendar_result()
        try:
            if identity(underlying)[1] != self.exchange:
                raise ValueError('CALENDAR_OWN_EXCHANGE_REQUIRED')
        except ValueError as exc:
            return dict(status='DATA_UNAVAILABLE', reason=str(exc), expiry=None,
                        classification=MISSING, production_calendar='NOT_INSTALLED',
                        auto_next='FAIL-CLOSED', preview_only=True)
        result = OfficialCalendars(self.evidence).preview(want, underlying, as_of,
                                                        instrument=instrument, cycle=cycle)
        result['classification'] = VALIDATED if result['status'] == 'OK' else MISSING
        return result
