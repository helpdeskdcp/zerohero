"""Offline acquisition tooling, not a production calendar loader.

Source bytes establish observations, never historical provider listing completeness.
No broker dependencies or default network transport. Fetching is explicit and
injected; every URL (including failures) is attempted at most once per cache.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, cast

import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import urlsplit
import zipfile

if TYPE_CHECKING:
    from app.hist_options.calendar_scope import require_calendar_exchange, calendar_disabled
elif __package__.startswith("backend."):
    from ..app.hist_options.calendar_scope import require_calendar_exchange, calendar_disabled
else:
    from app.hist_options.calendar_scope import require_calendar_exchange, calendar_disabled

MAX_BYTES = 64 * 1024 * 1024
OFFICIAL_HOSTS = {
    "NSE": {"archives.nseindia.com", "nsearchives.nseindia.com", "www.nseindia.com"},
    # api.bseindia.com is declared by the retained official appConfig.json;
    # accepting its origin does not validate any particular report endpoint.
    "BSE": {"www.bseindia.com", "api.bseindia.com"}, "MCX": {"www.mcxindia.com"},
}


@dataclass(frozen=True)
class SourceSpec:
    source_url: str
    source_class: str
    exchange: str
    dataset: str

    def validate(self):
        url = urlsplit(self.source_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("PUBLIC_HTTPS_SOURCE_REQUIRED")
        if self.source_class not in {"OFFICIAL_ARCHIVE", "SECONDARY_DATASET", "CURRENT_PROVIDER_MASTER"}:
            raise ValueError("SOURCE_CLASS_INVALID")
        if self.source_class == "OFFICIAL_ARCHIVE" and url.hostname not in OFFICIAL_HOSTS.get(self.exchange, set()):
            raise ValueError("OFFICIAL_ORIGIN_REQUIRED")
        if not self.dataset or self.exchange not in OFFICIAL_HOSTS:
            raise ValueError("SOURCE_IDENTITY_REQUIRED")


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class SourceCache:
    """Append-only URL cache. Incomplete/corrupt attempts require manual review.

    A transport callback returns (HTTP status, bytes, error code). It must use the
    requested public URL without redirects; acquire() does not implement network
    access. Import previously downloaded bytes with retain(). Neither path retries.
    Existing legacy source registers should be imported before any new acquisition.
    """
    def __init__(self, root: Path):
        self.root = Path(root)

    def acquire(self, spec: SourceSpec, fetch, *, acquired_at: str | None = None) -> dict:
        require_calendar_exchange(spec.exchange)
        spec.validate()
        key = digest(spec.source_url.encode())
        folder = self.root / key
        if folder.exists():
            if not (folder / "metadata.json").exists():
                raise ValueError("CACHE_INCOMPLETE_ATTEMPT")
            record = json.loads((folder / "metadata.json").read_text())
            if any(record[k] != v for k, v in asdict(spec).items()):
                raise ValueError("CACHE_IDENTITY_CONFLICT")
            raw = (folder / "raw.bin").read_bytes()
            if digest(raw) != record["sha256"] or len(raw) != record["bytes"]:
                raise ValueError("CACHE_HASH_MISMATCH")
            return record
        self.root.mkdir(parents=True, exist_ok=True)
        # mkdir without exist_ok serializes attempts: a crash never causes retry.
        folder.mkdir()
        try:
            status, raw, error = fetch(spec.source_url)
        except Exception as exc:
            status, raw, error = 0, b"", type(exc).__name__
        if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
            raise ValueError("SOURCE_SIZE_OR_TYPE_INVALID")
        record = dict(asdict(spec), cache_key=key,
                      acquired_at=acquired_at or datetime.now(timezone.utc).isoformat(),
                      http_status=status, sha256=digest(raw), bytes=len(raw), error=error,
                      acquisition_status="AVAILABLE" if status == 200 and not error else "FAILED",
                      permanent_failure=status in (403, 404), retry_attempted=False,
                      verification_status="UNVERIFIED", coverage_start=None, coverage_end=None)
        (folder / "raw.bin").write_bytes(raw)
        (folder / "metadata.json").write_text(json.dumps(record, indent=2) + "\n")
        return record

    def retain(self, spec: SourceSpec, raw: bytes, *, sha256: str, status=200,
               error="", acquired_at: str | None = None) -> dict:
        if digest(raw) != sha256:
            raise ValueError("SOURCE_HASH_MISMATCH")
        record = self.acquire(spec, lambda _: (status, raw, error), acquired_at=acquired_at)
        if record["sha256"] != sha256 or record["http_status"] != status:
            raise ValueError("SOURCE_VERSION_CONFLICT")
        return record


def _csv_bytes(raw: bytes) -> bytes:
    if len(raw) > MAX_BYTES:
        raise ValueError("ARCHIVE_TOO_LARGE")
    if raw.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            files = [f for f in archive.infolist() if f.filename.lower().endswith(".csv") and not f.is_dir()]
            if len(files) != 1 or files[0].file_size > MAX_BYTES:
                raise ValueError("ARCHIVE_MEMBER_AMBIGUOUS_OR_TOO_LARGE")
            # Read only; no filesystem extraction, including untrusted member paths.
            return archive.read(files[0])
    if raw.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("ARCHIVE_TOO_LARGE")
        return data
    return raw


def _day(value: str) -> str:
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%d%b%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError("INVALID_ARCHIVE_DATE")


def parse_archive(raw: bytes, spec: SourceSpec, *, archive_date: date | None = None,
                  secondary_identity: tuple[str, str] | None = None,
                  quarantine_invalid: bool = False) -> dict:
    """Parse known CSV layouts; no epoch/column guessing or missing-date filling.

    archive_date is the declared date of a dated master, NOT its publication time.
    secondary_identity is allowed only for a dataset whose accompanying description
    supplies the missing (underlying, instrument type). It is recorded as a claim.
    All date coverage is observed rows only, never a continuous verified interval.
    quarantine_invalid explicitly retains row numbers/reasons of rejected rows;
    their original contents remain in the hashed source. Default is strict rejection.
    """
    spec.validate()
    reader = csv.DictReader(io.StringIO(_csv_bytes(raw).decode("utf-8-sig")))
    fields = reader.fieldnames or []
    layout: dict[str, str | None]
    if "TckrSymb" in fields:
        layout = {"symbol": "TckrSymb", "kind": "FinInstrmTp", "expiry": "XpryDt", "day": "TradDt", "strike": "StrkPric", "side": "OptnTp"}
    elif "SYMBOL" in fields:
        layout = {"symbol": "SYMBOL", "kind": "INSTRUMENT", "expiry": "EXPIRY_DT", "day": "TIMESTAMP", "strike": "STRIKE_PR", "side": "OPTION_TYP"}
    elif "Symbol" in fields:
        layout = {"symbol": "Symbol", "kind": "Instrument", "expiry": "ExpiryDate", "day": None, "strike": "StrikePrice", "side": "OptionType"}
    elif secondary_identity and spec.source_class == "SECONDARY_DATASET":
        layout = {"symbol": None, "kind": None, "expiry": "Expiry", "day": "Date", "strike": "Strike Price", "side": "Option Type"}
    else:
        raise ValueError("UNSUPPORTED_ARCHIVE_SCHEMA")
    if any(k not in fields for k in layout.values() if k):
        raise ValueError("UNSUPPORTED_ARCHIVE_SCHEMA")
    if layout["day"] is None and archive_date is None:
        raise ValueError("OBSERVATION_DATE_REQUIRED")
    kinds = {"OPTIDX": "OPTION", "OPTSTK": "OPTION", "OPTFUT": "OPTION", "IDO": "OPTION", "STO": "OPTION", "OPTION": "OPTION",
             "FUTIDX": "FUTURE", "FUTSTK": "FUTURE", "FUTCOM": "FUTURE", "IDF": "FUTURE", "STF": "FUTURE", "FUTURE": "FUTURE"}
    observations, rejected = [], []

    def observation(row, line):
        if None in row or any(value is None for value in row.values()):
            raise ValueError("MALFORMED_ARCHIVE_ROW")
        if row.get("Src", spec.exchange).strip() != spec.exchange:
            raise ValueError("EXCHANGE_MISMATCH")
        expiry = _day(row[layout["expiry"]])
        if row.get("FininstrmActlXpryDt") and _day(row["FininstrmActlXpryDt"]) != expiry:
            raise ValueError("EXPIRY_FIELDS_CONFLICT")
        symbol = row[layout["symbol"]].strip() if layout["symbol"] else cast(tuple[str, str], secondary_identity)[0]
        kind = row[layout["kind"]].strip() if layout["kind"] else cast(tuple[str, str], secondary_identity)[1]
        if not symbol or kind not in kinds:
            raise ValueError("CONTRACT_IDENTITY_INVALID")
        day = _day(row[layout["day"]]) if layout["day"] else cast(date, archive_date).isoformat()
        return dict(exchange=spec.exchange, underlying=symbol, instrument=kinds[kind],
            raw_instrument=kind, expiry=expiry, observed_on=day, option_type=row[layout["side"]],
            strike=row[layout["strike"]], row_number=line)

    for line, row in enumerate(reader, 2):
        try:
            observations.append(observation(row, line))
        except ValueError as exc:
            if not quarantine_invalid:
                raise
            rejected.append(dict(row_number=line, reason=str(exc)))
    days = sorted({r["observed_on"] for r in observations})
    return dict(asdict(spec), source_sha256=digest(raw), provider=None,
                verification_status="UNVERIFIED", confidence="OBSERVATIONS_NOT_AUTHENTICATED",
                evidence_type="CONTRACT_OBSERVATIONS", observations=observations, rejected_rows=rejected,
                observed_dates=days, coverage_start=min(days) if days else None,
                coverage_end=max(days) if days else None, continuous_coverage_proven=False,
                date_basis="CSV_ROW" if layout["day"] else "CALLER_DECLARED_ARCHIVE_DATE",
                identity_basis="DATASET_DESCRIPTION" if not layout["symbol"] else "CSV_ROW",
                publication_at=None, listing_complete=False)


def observed_expiries(inventories: list[dict], exchange: str, underlying: str,
                     instrument: str, observed_on: date, *, provider: str | None = None) -> dict:
    """Offline exact observation-date lookup, NOT historical AUTO/NEXT.

    An EOD row cannot establish availability earlier that day. This API deliberately
    accepts a date, not an as-of instant, and returns candidates with source evidence.
    No previous/next/symmetric date substitution, guessed cutoff or provider fallback.
    """
    if type(observed_on) is not date:
        raise ValueError("OBSERVATION_DATE_REQUIRED_NOT_ASOF")
    unavailable = dict(status="DATA_UNAVAILABLE", expiries=[])
    if provider is not None:
        return dict(unavailable, reason="PROVIDER_LISTING_NOT_ESTABLISHED")
    selected = []
    for inventory in inventories:
        if inventory["exchange"] != exchange:
            continue
        rows = [r for r in inventory["observations"] if
                (r["exchange"], r["underlying"], r["instrument"], r["observed_on"]) ==
                (exchange, underlying, instrument, observed_on.isoformat())]
        if rows:
            selected.append(dict(source_url=inventory["source_url"], source_sha256=inventory["source_sha256"],
                                 source_class=inventory["source_class"], expiries=sorted({r["expiry"] for r in rows})))
    if not selected:
        return dict(unavailable, reason="EXACT_OBSERVATION_UNAVAILABLE")
    # Keep source sets distinct: a subset is not proof of disagreement/completeness.
    return dict(status="OBSERVED_CANDIDATES", verification_status="UNVERIFIED", provider=None,
                expiries=sorted({e for s in selected for e in s["expiries"]}), sources=selected,
                reason="NOT_PROVIDER_CALENDAR_OR_INTRADAY_ASOF_EVIDENCE")


def holiday_candidate(nominal: date, sessions: dict[date, bool], *, effective_from: date,
                      effective_to: date, rule_reference: str, holiday_reference: str) -> dict:
    """Candidate-only previous-trading-day adjustment under supplied dated evidence.

    No built-in weekday rule/holiday calendar. Closed days and the eventual open day
    must all be explicitly supplied; a missing day is unavailable, not a weekend.
    No production rule is installed and this function never marks a date VERIFIED.
    """
    if not rule_reference or not holiday_reference or not effective_from <= nominal < effective_to:
        raise ValueError("RULE_EVIDENCE_UNAVAILABLE")
    candidate = nominal
    while candidate >= effective_from:
        if candidate not in sessions or type(sessions[candidate]) is not bool:
            raise ValueError("SESSION_EVIDENCE_MISSING")
        if sessions[candidate]:
            return dict(expiry=candidate.isoformat(), nominal_date=nominal.isoformat(),
                        source_class="DERIVED_RULE", verification_status="UNVERIFIED",
                        rule_reference=rule_reference, holiday_reference=holiday_reference,
                        effective_from=effective_from.isoformat(), effective_to=effective_to.isoformat())
        candidate -= timedelta(days=1)
    raise ValueError("SESSION_EVIDENCE_MISSING")
