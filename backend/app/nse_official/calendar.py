"""Reviewed official exchange calendars, using the existing offline resolver.

No default evidence, network lookup, date generation or production installer.
A VERIFIED attestation must independently substantiate every cited claim. Source
hashes alone do not prove semantic correctness. Inputs are trusted curator objects.
"""
from dataclasses import dataclass
import hashlib
import math
from typing import ClassVar
from urllib.parse import urlsplit

from ..hist_options.provider_calendar import (
    CalendarEvidence, ProviderCalendars, identity, instant, exact_date, ALIASES,
)

from ..hist_options.calendar_scope import calendar_disabled, disabled_calendar_result

HOSTS = {
    'NSE': frozenset({'www.nseindia.com','nsearchives.nseindia.com','archives.nseindia.com'}),
    'BSE': frozenset({'www.bseindia.com','api.bseindia.com'}),
    'MCX': frozenset({'www.mcxindia.com'}),
}
CYCLES = frozenset({'WEEKLY','MONTHLY','QUARTERLY','HALF_YEARLY'})


@dataclass(frozen=True)
class OfficialSource:
    url: str
    raw: bytes
    sha256: str
    retrieved_at: str
    independently_observed: bool

    def validate(self, exchange: str):
        parts = urlsplit(self.url)
        if (parts.scheme != 'https' or parts.hostname not in HOSTS.get(exchange, ())
                or parts.port not in (None,443) or parts.username or parts.password or parts.fragment
                or any(c.isspace() for c in self.url)):
            raise ValueError('CALENDAR_OFFICIAL_SOURCE_REQUIRED')
        if self.independently_observed is not True:
            raise ValueError('CALENDAR_SOURCE_NOT_OBSERVED')
        if not self.raw or hashlib.sha256(self.raw).hexdigest() != self.sha256:
            raise ValueError('CALENDAR_SOURCE_HASH_MISMATCH')
        instant(self.retrieved_at)


@dataclass(frozen=True)
class OfficialCalendarEvidence(CalendarEvidence):
    sources: tuple[OfficialSource, ...] = ()
    schema_version: ClassVar[str] = 'official-exchange-calendar/v1'

    def _provider_allowed(self, provider: str, exchange: str) -> bool:
        return exchange in HOSTS and provider == exchange + '_OFFICIAL'

    def validated(self) -> dict:
        m = super().validated()
        try:
            if not self.sources:
                raise ValueError('CALENDAR_OFFICIAL_SOURCE_REQUIRED')
            for source in self.sources:
                source.validate(m['exchange'])
            if len({s.url for s in self.sources}) != len(self.sources):
                raise ValueError('CALENDAR_SOURCE_CONFLICT')
            if not any(s.url == m['source'] and s.raw == self.source_artifact for s in self.sources):
                raise ValueError('CALENDAR_PRIMARY_SOURCE_MISMATCH')
            if instant(m['retrieved_at']) != max(instant(s.retrieved_at) for s in self.sources):
                raise ValueError('CALENDAR_RETRIEVAL_INVALID')
            if instant(m['published_at']) > instant(m['retrieved_at']):
                raise ValueError('CALENDAR_PUBLICATION_INVALID')
            age = m['max_age_seconds']
            if isinstance(age,bool) or not isinstance(age,(int,float)) or not math.isfinite(age) or age <= 0:
                raise ValueError('CALENDAR_FRESHNESS_POLICY_REQUIRED')
            expected = {'NSE':{'OPTION':'OPTIDX','FUTURE':'FUTIDX'},
                        'BSE':{'OPTION':'OPTIDX','FUTURE':'FUTIDX'},
                        'MCX':{'OPTION':'OPTFUT','FUTURE':'FUTCOM'}}
            if m['instrument_type'] != expected[m['exchange']][m['instrument']]:
                raise ValueError('CALENDAR_CONTRACT_TYPE_INVALID')
            hashes = {s.sha256 for s in self.sources}
            def claim(group, key):
                refs = group.get(key)
                if not isinstance(refs,list) or not refs or any(r not in hashes for r in refs):
                    raise ValueError('CALENDAR_CLAIM_EVIDENCE_REQUIRED')
            for key in ('completeness','contract_mapping','published_at','effective_interval'):
                claim(m['calendar_claims'],key)
            for entry in m['entries']:
                for key in ('expiry','listed_at','last_trading_at'):
                    claim(entry['claims'],key)
                cycles = entry.get('cycles')
                if cycles is not None:
                    if not isinstance(cycles,list) or not cycles or len(set(cycles)) != len(cycles) or not set(cycles) <= CYCLES:
                        raise ValueError('CALENDAR_CYCLE_INVALID')
                    claim(entry['claims'],'cycles')
            return m
        except (KeyError,TypeError,AttributeError) as exc:
            raise ValueError('CALENDAR_EVIDENCE_INVALID') from exc


@dataclass(frozen=True)
class OfficialCalendars(ProviderCalendars):
    """Preview resolution does not install a calendar or enable application AUTO/NEXT."""
    evidence: tuple[OfficialCalendarEvidence, ...] = ()

    def resolve(self, want, provider, underlying, as_of, *, instrument='OPTION', cycle=None):
        if any(not isinstance(e,OfficialCalendarEvidence) for e in self.evidence):
            raise ValueError('CALENDAR_OFFICIAL_SOURCE_REQUIRED')
        expiry, p = super().resolve(want,provider,underlying,as_of,instrument=instrument,cycle=cycle)
        evidence = next(e for e in self.evidence if e.manifest_sha256 == p['manifest_sha256'])
        m = evidence.validated()
        if (instant(as_of)-instant(m['published_at'])).total_seconds() > m['max_age_seconds']:
            raise ValueError('CALENDAR_STALE')
        p.update(retrieved_at=m['retrieved_at'], instrument_type=m['instrument_type'],
                 contract_type=m['instrument'], freshness_max_age_seconds=m['max_age_seconds'],
                 sources=[dict(source_url=s.url,sha256=s.sha256,retrieved_at=s.retrieved_at) for s in evidence.sources])
        return expiry,p

    def preview(self, want, underlying, as_of, *, instrument='OPTION', cycle=None) -> dict:
        out: dict = dict(status='DATA_UNAVAILABLE',reason=None,expiry=None,provenance={},
                   production_calendar='NOT_INSTALLED',auto_next='FAIL-CLOSED',preview_only=True)
        try:
            symbol, exchange = identity(underlying)
            if calendar_disabled(exchange):
                return disabled_calendar_result()
            want = str(want).upper()
            explicit = None if want in ALIASES else exact_date(want)
            expiry,p = self.resolve('CURRENT' if explicit else want,exchange+'_OFFICIAL',symbol,as_of,
                                    instrument=instrument,cycle=cycle)
            if explicit:
                if explicit.isoformat() not in p['available_expiries']:
                    raise ValueError('CALENDAR_EXPIRY_UNAVAILABLE')
                expiry = explicit
                m = next(e.validated() for e in self.evidence if e.manifest_sha256 == p['manifest_sha256'])
                entry = next(e for e in m['entries'] if exact_date(e['expiry']) == explicit)
                p.update(expiry=explicit.isoformat(),requested_alias=want,
                         listed_at=entry['listed_at'],last_trading_at=entry['last_trading_at'])
                if cycle is not None:p['cycles']=entry['cycles']
            return dict(out,status='OK',expiry=expiry.isoformat(),provenance=p)
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            return dict(out,reason=str(exc) if isinstance(exc,ValueError) else 'CALENDAR_EVIDENCE_INVALID')
