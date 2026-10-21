"""Offline provider calendars. No inferred dates, I/O, broker calls or migrations.

Evidence is a reviewed, normalized manifest bound to retained source artifacts.
The caller is a trusted repository curator, not an HTTP client. Hashes establish
integrity, NOT authenticity or correctness: VERIFIED is a review attestation.
No production calendar is shipped or implicitly loaded by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from typing import ClassVar

from .calendar_scope import require_calendar_exchange

SCHEMA_VERSION = "provider-calendar/v1"
ALIASES = frozenset({"AUTO", "CURRENT", "NEXT", "LATEST"})
IST = timezone(timedelta(hours=5, minutes=30))

# Identity aliases only, not expiry rules or a claim of provider availability.
# Sources: instruments._SEED, dhan_chain._UNDERLYING_SCRIP/_MCX_SYMBOLS.
EXCHANGES = {**dict.fromkeys(("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"), "NSE"),
             **dict.fromkeys(("SENSEX", "BANKEX"), "BSE"),
             **dict.fromkeys(("NATURALGAS", "NATGASMINI", "CRUDEOIL", "CRUDEOILMINI",
                              "GOLD", "GOLDMINI", "SILVER", "SILVERMINI"), "MCX")}
SYMBOL_ALIASES = {"NIFTY50": "NIFTY", "NIFTY 50": "NIFTY", "NIFTYBANK": "BANKNIFTY",
                  "BANK NIFTY": "BANKNIFTY", "NATGAS": "NATURALGAS", "NG": "NATURALGAS",
                  "NATURALGASMINI": "NATGASMINI", "NGMINI": "NATGASMINI",
                  "CRUDEOILM": "CRUDEOILMINI", "CRUDE OIL MINI": "CRUDEOILMINI"}


def identity(underlying: str) -> tuple[str, str]:
    symbol = str(underlying).strip().upper()
    symbol = SYMBOL_ALIASES.get(symbol, symbol)
    if symbol not in EXCHANGES:
        raise ValueError("CALENDAR_UNSUPPORTED_INSTRUMENT")
    return symbol, EXCHANGES[symbol]


def instant(value: str | datetime) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("CALENDAR_TIMEZONE_REQUIRED")
    return dt.astimezone(timezone.utc)


def exact_date(value: str) -> date:
    for fmt in ("%d%b%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).upper(), fmt).date()
        except ValueError:
            continue
    raise ValueError("INVALID_EXPIRY")


@dataclass(frozen=True)
class CalendarEvidence:
    """Retained manifest plus source bytes and a separate review attestation.

    Manifest fields: schema_version, calendar_version, provider, exchange,
    underlying, instrument (OPTION/FUTURE), source, source_sha256, published_at,
    effective_from, effective_to, complete, entries. Each entry contains expiry,
    listed_at and last_trading_at. All intervals are [from, to), timezone-aware.
    `complete` attests the entire listed expiry set during the effective interval.
    Raw evidence must substantiate listing, completeness AND trading cutoffs.
    Verification can happen later; published_at is when evidence was available
    to the market, not when we downloaded/reviewed it. Never backdate it.
    """
    manifest: bytes
    manifest_sha256: str
    source_artifact: bytes
    verification_status: str
    verified_by: str
    verification_reference: str

    schema_version: ClassVar[str] = SCHEMA_VERSION

    def _provider_allowed(self, provider: str, exchange: str) -> bool:
        return provider in ("DHAN", "ANGELONE")

    def validated(self) -> dict:
        if self.verification_status != "VERIFIED":
            raise ValueError("CALENDAR_NOT_VERIFIED")
        if not self.verified_by.strip() or not self.verification_reference.strip():
            raise ValueError("CALENDAR_REVIEW_MISSING")
        try:
            if hashlib.sha256(self.manifest).hexdigest() != self.manifest_sha256:
                raise ValueError("CALENDAR_MANIFEST_HASH_MISMATCH")
            m = json.loads(self.manifest)
            require_calendar_exchange(m.get("exchange", ""))
            if not self.source_artifact or hashlib.sha256(self.source_artifact).hexdigest() != m["source_sha256"]:
                raise ValueError("CALENDAR_SOURCE_HASH_MISMATCH")
            if m["schema_version"] != self.schema_version:
                raise ValueError("CALENDAR_SCHEMA_UNSUPPORTED")
            if m["complete"] is not True:
                raise ValueError("CALENDAR_INCOMPLETE")
            symbol, exchange = identity(m["underlying"])
            if (not self._provider_allowed(m["provider"], exchange) or m["exchange"] != exchange
                    or m["instrument"] not in ("OPTION", "FUTURE")):
                raise ValueError("CALENDAR_IDENTITY_INVALID")
            if not m["source"].strip() or not m["calendar_version"].strip():
                raise ValueError("CALENDAR_PROVENANCE_MISSING")
            start, end = instant(m["effective_from"]), instant(m["effective_to"])
            if start >= end:
                raise ValueError("CALENDAR_INTERVAL_INVALID")
            instant(m["published_at"])
            seen = set()
            for e in m["entries"]:
                expiry = exact_date(e["expiry"])
                listed, close = instant(e["listed_at"]), instant(e["last_trading_at"])
                if listed >= close or close.astimezone(IST).date() != expiry or expiry in seen:
                    raise ValueError("CALENDAR_ENTRY_INVALID")
                seen.add(expiry)
            return {**m, "underlying": symbol}
        except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            raise ValueError("CALENDAR_EVIDENCE_INVALID") from exc


@dataclass(frozen=True)
class ProviderCalendars:
    """Explicitly supplied versioned evidence; empty means unavailable."""
    evidence: tuple[CalendarEvidence, ...] = ()

    def resolve(self, want: str, provider: str, underlying: str, as_of: datetime,
                *, instrument: str = "OPTION", cycle: str | None = None) -> tuple[date, dict]:
        if want not in ALIASES:
            raise ValueError("CALENDAR_ALIAS_INVALID")
        cutoff = instant(as_of)
        symbol, exchange = identity(underlying)
        require_calendar_exchange(exchange)
        candidates = []
        for evidence in self.evidence:
            # Route by the manifest's claimed identity, then validate it before
            # trusting ANY date. Invalid Dhan evidence must not block AngelOne.
            try:
                header = json.loads(evidence.manifest)
                claimed_symbol, _ = identity(header["underlying"])
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if (header.get("provider"), header.get("exchange"), claimed_symbol, header.get("instrument")) != (
                    provider, exchange, symbol, instrument):
                continue
            # Use header dates only to reject irrelevant evidence. A future or
            # expired version must not change the answer to a past query.
            try:
                published = instant(header["published_at"])
                start, end = instant(header["effective_from"]), instant(header["effective_to"])
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise ValueError("CALENDAR_EVIDENCE_INVALID") from exc
            if start >= end:
                raise ValueError("CALENDAR_INTERVAL_INVALID")
            if published > cutoff or not start <= cutoff < end:
                continue
            m = evidence.validated()
            candidates.append((evidence, m))
        if not candidates:
            raise ValueError("CALENDAR_EVIDENCE_UNAVAILABLE")
        if len(candidates) != 1:
            # Conflicting/overlapping versions need review, never arbitrary order.
            raise ValueError("CALENDAR_VERSION_CONFLICT")
        evidence, m = candidates[0]
        eligible = [e for e in m["entries"]
                    if instant(e["listed_at"]) <= cutoff < instant(e["last_trading_at"])]
        if cycle is not None:
            if cycle not in ("WEEKLY", "MONTHLY", "QUARTERLY", "HALF_YEARLY"):
                raise ValueError("CALENDAR_CYCLE_INVALID")
            if any(not isinstance(e.get("cycles"), list) or not e["cycles"] for e in eligible):
                raise ValueError("CALENDAR_CYCLE_EVIDENCE_UNAVAILABLE")
            eligible = [e for e in eligible if cycle in e["cycles"]]
        dates = sorted(exact_date(e["expiry"]) for e in eligible)
        index = 1 if want == "NEXT" else -1 if want == "LATEST" else 0
        if not dates or (want == "NEXT" and len(dates) < 2):
            raise ValueError("CALENDAR_EXPIRY_UNAVAILABLE")
        provenance = {k: m[k] for k in ("schema_version", "calendar_version", "provider", "exchange",
            "underlying", "instrument", "source", "source_sha256", "published_at",
            "effective_from", "effective_to")}
        provenance.update(verification_status=evidence.verification_status,
            verified_by=evidence.verified_by, verification_reference=evidence.verification_reference,
            manifest_sha256=evidence.manifest_sha256, requested_alias=want,
            as_of=cutoff.isoformat(), expiry=dates[index].isoformat())
        entry = next(e for e in m["entries"] if exact_date(e["expiry"]) == dates[index])
        provenance.update(listed_at=entry["listed_at"], last_trading_at=entry["last_trading_at"])
        provenance["available_expiries"] = [d.isoformat() for d in dates]
        if cycle is not None:
            provenance.update(requested_cycle=cycle, cycles=entry["cycles"])
        return dates[index], provenance


def resolve(want, provider, underlying, as_of, evidence=None) -> tuple[date, dict]:
    """Exact dates bypass discovery. Aliases require reviewed provider evidence.

    `expiry_dates` legacy keyword callers must now pass ProviderCalendars;
    a bare list/mapping is not verified evidence. No default calendar is guessed.
    """
    symbol = str(underlying).strip().upper()
    require_calendar_exchange(EXCHANGES.get(SYMBOL_ALIASES.get(symbol, symbol), ""))
    want = str(want or "AUTO").upper()
    if want not in ALIASES:
        return exact_date(want), {"verification_status": "EXPLICIT_NOT_CALENDAR_VERIFIED",
                                 "provider": provider, "expiry": exact_date(want).isoformat()}
    if not isinstance(evidence, ProviderCalendars):
        raise ValueError("CALENDAR_EVIDENCE_UNAVAILABLE")
    return evidence.resolve(want, provider, underlying, as_of)
