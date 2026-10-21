"""Synthetic calendars for tests only. These dates are NOT exchange evidence."""
import hashlib
import json

from app.hist_options.provider_calendar import CalendarEvidence, ProviderCalendars, SCHEMA_VERSION, identity


def synthetic_evidence(provider="DHAN", underlying="NIFTY", expiries=("08SEP2026", "15SEP2026"), **overrides):
    from app.hist_options.provider_calendar import exact_date
    symbol, exchange = identity(underlying)
    raw = b'SYNTHETIC provider list, listing times and trading cutoffs; unit tests only'
    manifest = dict(schema_version=SCHEMA_VERSION, calendar_version="synthetic-v1",
        provider=provider, exchange=exchange, underlying=symbol, instrument="OPTION",
        source="test-fixture://synthetic-provider-calendar", source_sha256=hashlib.sha256(raw).hexdigest(),
        published_at="2026-09-01T00:00:00+05:30", effective_from="2026-09-01T00:00:00+05:30",
        effective_to="2026-10-01T00:00:00+05:30", complete=True,
        entries=[dict(expiry=exact_date(e).isoformat(), listed_at="2026-09-01T00:00:00+05:30",
                      last_trading_at=exact_date(e).isoformat()+"T15:30:00+05:30") for e in expiries])
    manifest.update(overrides)
    payload = json.dumps(manifest, sort_keys=True).encode()
    return CalendarEvidence(payload, hashlib.sha256(payload).hexdigest(), raw, "VERIFIED",
                            "synthetic-test-fixture", "test-fixture://review-not-real-verification")


def synthetic_calendars(mapping, **overrides):
    return ProviderCalendars(tuple(synthetic_evidence(provider=p, expiries=dates, **overrides)
                                   for p, dates in mapping.items()))
