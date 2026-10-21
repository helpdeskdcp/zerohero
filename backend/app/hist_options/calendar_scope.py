"""Product scope, independent of evidence quality. MCX is retained history only.

This policy applies to calendar acquisition, validation and resolution. It does
not change broker configuration or erase archival evidence. No caller-supplied
attestation or evidence can override a disabled exchange.
"""
DISABLED_EXCHANGES = frozenset({'MCX'})
DISABLED_REASON = 'CALENDAR_EXCHANGE_DISABLED'


def calendar_disabled(exchange: object) -> bool:
    return str(exchange).strip().upper() in DISABLED_EXCHANGES


def require_calendar_exchange(exchange: object) -> None:
    if calendar_disabled(exchange):
        raise ValueError(DISABLED_REASON)


def disabled_calendar_result() -> dict:
    return dict(status='DATA_UNAVAILABLE', reason=DISABLED_REASON, expiry=None,
                scope='OUT_OF_SCOPE', scope_status='DISABLED', production='NOT_ENABLED',
                production_calendar='NOT_INSTALLED', auto_next='DISABLED',
                pending_requirements=[], classification='HISTORICAL_RETAINED_ONLY',
                provenance={}, preview_only=True)
