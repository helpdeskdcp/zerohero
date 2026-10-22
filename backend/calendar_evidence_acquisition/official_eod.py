"""Evidence-only exchange EOD pipeline; no broker or production calendar integration.

Download authorization is an exact link in retained successful official discovery
bytes, never a generated filename pattern. The existing SourceCache owns bytes,
checksums and duplicate/failure retention. Verification is a separate review gate.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, cast

from dataclasses import asdict, dataclass
from datetime import date
from html.parser import HTMLParser
import json
from pathlib import Path
import csv
import io
import gzip
import zipfile
import zlib
from urllib.parse import unquote, urljoin, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .static_archives import SourceCache, SourceSpec, _csv_bytes, _day, digest, OFFICIAL_HOSTS, MAX_BYTES

if TYPE_CHECKING:
    from app.hist_options.calendar_scope import require_calendar_exchange, calendar_disabled
elif __package__.startswith("backend."):
    from ..app.hist_options.calendar_scope import require_calendar_exchange, calendar_disabled
else:
    from app.hist_options.calendar_scope import require_calendar_exchange, calendar_disabled

PARSER_VERSION = "exchange-eod/v1"
NSE_UDIFF_START = date(2024, 7, 8)  # Required format transition; not an expiry rule.


@dataclass(frozen=True)
class Discovery:
    source_url: str
    http_status: int
    body: bytes
    sha256: str
    acquired_at: str


@dataclass(frozen=True)
class Report:
    exchange: str
    segment: str
    trading_date: date
    source_url: str
    report_name: str
    original_filename: str
    discovery: Discovery
    evidence_classification: str = "OFFICIAL_EXCHANGE_EOD"


def _official(url: str, exchange: str):
    SourceSpec(url, "OFFICIAL_ARCHIVE", exchange, "EXCHANGE_REPORT").validate()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_official(url: str) -> tuple[int, bytes, str]:
    """One unauthenticated GET to an allowlisted exchange, no redirects/retries.

    Called only after report discovery validation. No broker hosts, credentials,
    cookies, environment proxy credentials, or credential files are loaded.
    """
    exchange = next((venue for venue, hosts in OFFICIAL_HOSTS.items()
                     if urlsplit(url).hostname in hosts), None)
    if exchange is None:
        raise ValueError("OFFICIAL_ORIGIN_REQUIRED")
    require_calendar_exchange(exchange)
    _official(url, exchange)
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    request = Request(url, headers={"User-Agent": "ZeroHero-public-evidence/1"}, method="GET")
    try:
        with opener.open(request, timeout=20) as response:
            payload = response.read(MAX_BYTES + 1)
            if len(payload) > MAX_BYTES:
                raise ValueError("SOURCE_TOO_LARGE")
            return response.status, payload, ""
    except HTTPError as exc:
        with exc:
            payload = exc.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise ValueError("SOURCE_TOO_LARGE")
        return exc.code, payload, "HTTP_STATUS_FAILURE"
    except (URLError, TimeoutError):
        return 0, b"", "TRANSPORT_FAILURE"


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.extend(v for k, v in attrs if k == "href" and v)


def discover_reports(discovery: Discovery, exchange: str, segment: str,
                     trading_date: date, report_name: str, *,
                     evidence_classification="OFFICIAL_EXCHANGE_EOD") -> list[Report]:
    """Extract exact download URLs from official HTML anchors or JSON URL values.

    Report date/name are request context and must be checked against parsed rows.
    Bare filenames, JavaScript templates and site shells cannot authorize a URL.
    A new endpoint adapter requires retained official evidence, not guessing.
    """
    _official(discovery.source_url, exchange)
    if discovery.http_status != 200:
        raise ValueError("DISCOVERY_UNAVAILABLE")
    if digest(discovery.body) != discovery.sha256:
        raise ValueError("DISCOVERY_HASH_MISMATCH")
    if evidence_classification not in {"OFFICIAL_EXCHANGE_EOD", "OFFICIAL_EXCHANGE_CONTRACT"}:
        raise ValueError("EVIDENCE_CLASS_INVALID")
    if not segment or not report_name or type(trading_date) is not date:
        raise ValueError("REPORT_CONTEXT_REQUIRED")
    text = discovery.body.decode("utf-8-sig")
    links = _Links()
    links.feed(text)
    urls = list(links.hrefs)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = None

    def walk(value):
        if isinstance(value, str) and value.startswith(("https://", "/")):
            urls.append(value)
        elif isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(obj)
    found = {}
    for value in urls:
        url = urljoin(discovery.source_url, value)
        try:
            _official(url, exchange)
        except ValueError:
            continue
        path = urlsplit(url).path
        if not path.lower().endswith((".csv", ".csv.gz", ".zip")):
            continue
        found[url] = Report(exchange, segment, trading_date, url, report_name,
                            unquote(path.rsplit("/", 1)[-1]), discovery, evidence_classification)
    return list(found.values())


def _validate_report(report: Report):
    candidates = discover_reports(report.discovery, report.exchange, report.segment,
                                  report.trading_date, report.report_name,
                                  evidence_classification=report.evidence_classification)
    if report not in candidates:
        raise ValueError("REPORT_NOT_DISCOVERED")


@dataclass(frozen=True)
class FieldMap:
    """Explicit column mapping; version and raw column names retained in inventory.

    Do not install BSE/MCX mappings until their actual official file is inspected.
    Expiry, maturity and actual-expiry columns have different semantics.
    """
    version: str
    trading_date: str
    underlying: str
    instrument: str
    expiry: str
    maturity: str | None = None
    identifier: str | None = None
    actual_expiry: str | None = None
    exchange: str | None = None
    segment: str | None = None
    strike: str | None = None
    option_type: str | None = None


NSE_LEGACY = FieldMap("nse-legacy-fo/v1", "TIMESTAMP", "SYMBOL", "INSTRUMENT", "EXPIRY_DT",
                      strike="STRIKE_PR", option_type="OPTION_TYP")
NSE_UDIFF = FieldMap("nse-udiff-fo/v1", "TradDt", "TckrSymb", "FinInstrmTp", "XpryDt",
                     identifier="FinInstrmId", actual_expiry="FininstrmActlXpryDt", exchange="Src",
                     segment="Sgmt", strike="StrkPric", option_type="OptnTp")


def parse_eod(raw: bytes, report: Report, fields: FieldMap) -> dict:
    _validate_report(report)
    return _parse_eod_records(raw, report, fields)


@dataclass(frozen=True)
class _StagedContext:
    exchange: str
    segment: str
    trading_date: date
    source_url: str
    evidence_classification: str = "UNVERIFIED"


def parse_staged_eod(raw: bytes, *, exchange: str, segment: str, trading_date: date,
                     provenance_url: str, fields: FieldMap) -> dict:
    """Parse operator-supplied bytes without fabricating official discovery proof.

    The URL is a claim until independently reviewed; every record stays UNVERIFIED.
    This entry point cannot authorize a network request or a production calendar.
    """
    _official(provenance_url, exchange)
    return _parse_eod_records(raw, _StagedContext(exchange, segment, trading_date, provenance_url), fields)


def _parse_eod_records(raw: bytes, report: Report | _StagedContext, fields: FieldMap) -> dict:
    if report.exchange == "NSE" and report.evidence_classification != "OFFICIAL_EXCHANGE_CONTRACT":
        expected = NSE_UDIFF if report.trading_date >= NSE_UDIFF_START else NSE_LEGACY
        if fields != expected:
            raise ValueError("NSE_FORMAT_PERIOD_MISMATCH")
    reader = csv.DictReader(io.StringIO(_csv_bytes(raw).decode("utf-8-sig")))
    mapping = asdict(fields)
    if not fields.version or any(column not in (reader.fieldnames or [])
                                 for key, column in mapping.items() if key != "version" and column):
        raise ValueError("REQUIRED_FIELD_MISSING")
    rows, seen, duplicates = [], {}, []
    for line, row in enumerate(reader, 2):
        if None in row or any(v is None for v in row.values()):
            raise ValueError("MALFORMED_ARCHIVE_ROW")
        day = _day(row[fields.trading_date])
        if day != report.trading_date.isoformat():
            raise ValueError("TRADING_DATE_MISMATCH")
        if fields.exchange and row[fields.exchange].strip() != report.exchange:
            raise ValueError("EXCHANGE_MISMATCH")
        if fields.segment and row[fields.segment].strip() != report.segment:
            raise ValueError("SEGMENT_MISMATCH")
        symbol, instrument = row[fields.underlying].strip(), row[fields.instrument].strip()
        if not symbol or not instrument:
            raise ValueError("CONTRACT_IDENTITY_INVALID")
        # Never coalesce maturity/actual-expiry into expiry, even if expiry is missing.
        item = dict(exchange=report.exchange, segment=report.segment, trading_date=day,
                    underlying=symbol, instrument=instrument, expiry_date=_day(row[fields.expiry]),
                    contract_maturity_date=_day(row[fields.maturity]) if fields.maturity and row[fields.maturity].strip() else None,
                    actual_expiry_date=_day(row[fields.actual_expiry]) if fields.actual_expiry and row[fields.actual_expiry].strip() else None,
                    contract_identifier=row[fields.identifier].strip() or None if fields.identifier else None,
                    product_identifier=symbol,
                    strike=row[fields.strike] if fields.strike else None,
                    option_type=row[fields.option_type] if fields.option_type else None,
                    evidence_classification=report.evidence_classification, provider=None,
                    observation_type="PUBLISHED_RECORD_NOT_PROOF_OF_TRADE", row_number=line,
                    raw_fields=row)
        signature = json.dumps(row, sort_keys=True)
        if signature in seen:
            duplicates.append(line)
        seen[signature] = line
        rows.append(item)
    result = dict(parser_version=PARSER_VERSION, field_map=mapping, source_url=report.source_url,
                  source_sha256=digest(raw), evidence_classification=report.evidence_classification,
                  observations=rows, duplicate_rows=duplicates, verification_status="UNVERIFIED",
                  provider_listing_status="UNVERIFIED", complete_calendar=False,
                  covered_trading_dates=[report.trading_date.isoformat()] if rows else [])
    conflicts = reconcile([result])
    result.update(verification_status=conflicts["verification_status"], conflicts=conflicts["conflicts"])
    return result


def filter_observations(inventory: dict, exchange: str, underlyings: set[str], instrument: str) -> list[dict]:
    return [r for r in inventory["observations"] if
            r["exchange"] == exchange and r["underlying"] in underlyings and r["instrument"] == instrument]


def reconcile(inventories: list[dict]) -> dict:
    """Compare same-day explicit contract IDs, not unrelated expiry subsets.

    No conflicting-ID conclusion is possible without a source contract identifier.
    Across trading dates, an amendment may be valid; that requires separate review.
    """
    groups: dict = {}
    for inv in inventories:
        for row in inv["observations"]:
            if not row["contract_identifier"]:
                continue
            key = (row["exchange"], row["segment"], row["trading_date"], row["contract_identifier"])
            value = (row["underlying"], row["instrument"], row["expiry_date"], row["contract_maturity_date"], row["actual_expiry_date"])
            groups.setdefault(key, {}).setdefault(value, []).append(dict(source_url=inv["source_url"],
                                                                        sha256=inv["source_sha256"], row_number=row["row_number"]))
    conflicts = [dict(contract_key=list(k), claims=[dict(values=list(v), provenance=p) for v, p in claims.items()])
                 for k, claims in groups.items() if len(claims) > 1]
    return dict(verification_status="CONFLICTING" if conflicts else "UNVERIFIED", conflicts=conflicts)


class EODPipeline:
    """Read-only remote acquisition; local append-only evidence output only.

    Transport is replaceable, unauthenticated GET only, without redirects. No endpoint
    templates, range fan-out, retry loop, broker calls or database operations exist.
    """
    def __init__(self, cache_root: Path):
        self.cache = SourceCache(cache_root)

    def acquire(self, report: Report, fields: FieldMap, fetch=fetch_official) -> dict:
        require_calendar_exchange(report.exchange)
        _validate_report(report)  # Before any cache mutation or network request.
        spec = SourceSpec(report.source_url, "OFFICIAL_ARCHIVE", report.exchange, report.evidence_classification)
        record = self.cache.acquire(spec, fetch)
        source = dict(record, exchange=report.exchange, segment=report.segment,
                      trading_date=report.trading_date.isoformat(), source_endpoint=report.discovery.source_url,
                      report_name=report.report_name, original_filename=report.original_filename,
                      discovery_sha256=report.discovery.sha256, discovery_acquired_at=report.discovery.acquired_at,
                      parser_version=PARSER_VERSION, field_map_version=fields.version)
        folder = self.cache.root / record["cache_key"]
        if record["acquisition_status"] != "AVAILABLE":
            result = dict(source=source, observations=[], verification_status="UNVERIFIED",
                          evidence_classification="UNVERIFIED", reason="SOURCE_ACQUISITION_FAILED")
        else:
            try:
                result = dict(parse_eod((folder / "raw.bin").read_bytes(), report, fields), source=source)
            except (ValueError, UnicodeError, csv.Error, zipfile.BadZipFile,
                    gzip.BadGzipFile, EOFError, zlib.error) as exc:
                result = dict(source=source, observations=[], verification_status="UNVERIFIED",
                              evidence_classification="UNVERIFIED", reason="PARSER_REJECTED", error=str(exc))
        # Retain discovery proof and mapping with each result. Never overwrite bytes.
        proof = folder / (report.discovery.sha256 + ".discovery")
        _write_once(proof, report.discovery.body)
        key = digest(json.dumps(dict(source=source, field_map=asdict(fields)), sort_keys=True).encode())
        _write_once(folder / ("eod_" + key + ".json"), (json.dumps(result, indent=2) + "\n").encode())
        return result


def _write_once(path: Path, raw: bytes):
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("EVIDENCE_OUTPUT_CONFLICT")
    else:
        with path.open("xb") as output:
            output.write(raw)


def required_windows() -> dict[str, tuple[str, str, str]]:
    """Owner-requested date intervals; not an inferred trading-day calendar."""
    return {
        "NIFTY": ("NSE", "2026-07-01", "2026-10-01"),
        "BANKNIFTY": ("NSE", "2026-07-24", "2026-10-01"),
        "FINNIFTY": ("NSE", "2026-09-04", "2026-10-02"),
        "MIDCPNIFTY": ("NSE", "2026-09-04", "2026-10-02"),
        "BANKEX": ("BSE", "2026-09-04", "2026-10-02"),
        "SENSEX": ("BSE", "2026-09-03", "2026-10-02"),
    }
