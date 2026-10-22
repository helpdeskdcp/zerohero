"""Browser/manual evidence acquisition CLI; no production calendar activation.

Run from backend/: python -m calendar_evidence_acquisition.evidence_engine --help
Archive hashes prove integrity, not exchange authenticity, completeness or broker
listing history. A successful ledger write never constitutes calendar verification.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, cast

import argparse
import asyncio
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, timezone
import fcntl
import gzip
import csv
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qsl, urljoin, urlsplit
from urllib.robotparser import RobotFileParser
import zipfile
import zlib

if TYPE_CHECKING:
    from app.hist_options.provider_calendar import EXCHANGES
elif __package__.startswith("backend."):
    from ..app.hist_options.provider_calendar import EXCHANGES
else:
    from app.hist_options.provider_calendar import EXCHANGES
from .official_eod import FieldMap, NSE_LEGACY, NSE_UDIFF, NSE_UDIFF_START, parse_staged_eod, reconcile, _official
from .static_archives import MAX_BYTES, digest
from .staged_formats import table_bytes, detect_fields, trading_date_from_rows

if TYPE_CHECKING:
    from app.hist_options.calendar_scope import require_calendar_exchange, calendar_disabled
elif __package__.startswith("backend."):
    from ..app.hist_options.calendar_scope import require_calendar_exchange, calendar_disabled
else:
    from app.hist_options.calendar_scope import require_calendar_exchange, calendar_disabled

LOG = logging.getLogger(__name__)
ROOT_PAGES = {x: f"https://www.{x.lower()}india.com/" for x in ("NSE", "BSE", "MCX")}
SCHEMA = "exchange-evidence-ledger/v1"
ROBOT_AGENT = "ZeroHero-public-evidence"


class BrowserUnavailable(RuntimeError):
    """Bounded browser acquisition failed; manual ingestion remains available."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def guarded_url(url: str, exchange: str) -> str:
    _official(url, exchange)
    sensitive = {"token", "access_token", "api_key", "apikey", "secret", "password", "authorization"}
    if any(key.lower() in sensitive for key, _ in parse_qsl(urlsplit(url).query)):
        raise ValueError("CREDENTIAL_BEARING_URL_REJECTED")
    return url


def robots_policy(status: int, body: str, exchange: str) -> RobotFileParser:
    """Respect public robots exclusions; uncertain/rate-limited cases need an operator."""
    policy = RobotFileParser(ROOT_PAGES[exchange] + "robots.txt")
    if status in {404, 410}:
        policy.parse([])
    elif status == 200 and len(body) <= 1024 * 1024:
        # Some exchange SPA routes return the application shell for robots.txt.
        # RobotFileParser would interpret HTML as an empty allow-all policy.
        if re.search(r"<(?:!doctype\s+html|html|head|body|script)\b", body, re.I):
            raise BrowserUnavailable("ROBOTS_POLICY_INVALID")
        policy.parse(body.splitlines())
    else:
        raise BrowserUnavailable("ROBOTS_POLICY_UNAVAILABLE")
    # A rendered page may load many resources; don't pretend to enforce a site's
    # crawl-delay/request-rate with only a delay between top-level navigations.
    if policy.crawl_delay(ROBOT_AGENT) is not None or policy.request_rate(ROBOT_AGENT) is not None:
        raise BrowserUnavailable("ROBOTS_RATE_POLICY_MANUAL_REQUIRED")
    return policy


def identify_filename(name: str, requested_date: date | None = None) -> tuple[str | None, date]:
    """Names supply claims only; never construct URLs or authenticate archives.

    Unknown formats require an explicit metadata date. Two-digit BSE years require
    full-year metadata rather than an assumed century. Rows must match that date.
    """
    match = re.fullmatch(r"BhavCopy_(NSE|BSE|MCX)_[A-Z]+_0_0_0_(\d{8})_F_0000\.csv(?:\.zip)?", name, re.I)
    if match:
        exchange, day = match[1].upper(), datetime.strptime(match[2], "%Y%m%d").date()
    elif match := re.fullmatch(r"fo(\d{2}[A-Za-z]{3}\d{4})bhav\.csv(?:\.zip)?", name, re.I):
        exchange, day = "NSE", datetime.strptime(match[1], "%d%b%Y").date()
    elif match := re.fullmatch(r"df(\d{6})\.zip", name, re.I):
        if requested_date is None:
            raise ValueError("EXPLICIT_DATE_REQUIRED_FOR_TWO_DIGIT_YEAR")
        if match[1] != requested_date.strftime("%d%m%y"):
            raise ValueError("FILENAME_DATE_MISMATCH")
        exchange, day = "BSE", requested_date
    else:
        if requested_date is None:
            raise ValueError("EXPLICIT_DATE_REQUIRED_FOR_UNKNOWN_FILENAME")
        return None, requested_date
    if requested_date is not None and day != requested_date:
        raise ValueError("FILENAME_DATE_MISMATCH")
    return exchange, day


def select_link(links: list[str], exchange: str, trading_date: date, exact_url: str | None = None) -> str:
    """Select one observed official link, not a speculative date URL pattern."""
    found = set()
    for url in links:
        try:
            guarded_url(url, exchange)
            if exact_url:
                if url == exact_url:
                    found.add(url)
            else:
                venue, day = identify_filename(Path(urlsplit(url).path).name, trading_date)
                if venue == exchange and day == trading_date:
                    found.add(url)
        except ValueError:
            continue
    if len(found) != 1:
        raise BrowserUnavailable("NO_UNIQUE_REPORT_LINK")
    return found.pop()


def playwright_download(*, exchange: str, trading_date: date, report_page: str,
                        download_url: str | None = None) -> dict:
    """Normal isolated Chromium session; no stealth, challenge solving or retries.

    Cookies stay in memory. Only official same-exchange GET/HEAD requests pass the
    route guard. Browser subprocess/network execution is opt-in via the CLI.
    """
    require_calendar_exchange(exchange)
    guarded_url(report_page, exchange)
    try:
        from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout, Error as PlaywrightError
    except ImportError as exc:
        raise BrowserUnavailable("PLAYWRIGHT_NOT_INSTALLED") from exc
    async def capture():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, timeout=20000)
            try:
                context = await browser.new_context(accept_downloads=True, service_workers="block", user_agent=ROBOT_AGENT + "/1")
                try:
                    policies = {}
                    def origin(url):
                        parsed = urlsplit(url)
                        return f"{parsed.scheme}://{parsed.netloc}"
                    async def policy_for(url):
                        guarded_url(url, exchange)
                        host = origin(url)
                        if host not in policies:
                            response = await context.request.get(host + "/robots.txt", timeout=10000, max_redirects=0)
                            policies[host] = robots_policy(response.status, await response.text(), exchange)
                        return policies[host]
                    async def gate(route):
                        try:
                            guarded_url(route.request.url, exchange)
                            if route.request.method not in {"GET", "HEAD"}:
                                raise ValueError("READ_ONLY_REQUEST_REQUIRED")
                            policy = policies.get(origin(route.request.url))
                            # A subdomain's exclusions are not implied by its parent.
                            # Unreviewed resource origins are blocked, not auto-probed.
                            if policy is None or not policy.can_fetch(ROBOT_AGENT, route.request.url):
                                raise ValueError("ROBOTS_DISALLOWED")
                        except ValueError:
                            await route.abort()
                        else:
                            await route.continue_()
                    await context.route("**/*", gate)
                    page = await context.new_page()
                    page.set_default_timeout(20000)
                    for url in dict.fromkeys([ROOT_PAGES[exchange], report_page]):
                        policy = await policy_for(url)
                        if not policy.can_fetch(ROBOT_AGENT, url):
                            raise BrowserUnavailable("ROBOTS_DISALLOWED")
                        response = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                        if response is None or response.status != 200:
                            raise BrowserUnavailable("OFFICIAL_PAGE_UNAVAILABLE")
                    await page.wait_for_selector("a[href]", timeout=10000)
                    anchors = await page.locator("a[href]").all()
                    urls = [urljoin(page.url, await anchor.get_attribute("href") or "") for anchor in anchors]
                    chosen = select_link(urls, exchange, trading_date, download_url)
                    policy = await policy_for(chosen)
                    if not policy.can_fetch(ROBOT_AGENT, chosen):
                        raise BrowserUnavailable("ROBOTS_DISALLOWED")
                    snapshot = (await page.content()).encode()
                    statuses = {}
                    page.on("response", lambda response: statuses.update({response.url: response.status}))
                    async with page.expect_download(timeout=30000) as pending:
                        await anchors[urls.index(chosen)].click(timeout=10000)
                    download = await pending.value
                    guarded_url(download.url, exchange)
                    if download.url != chosen:
                        raise BrowserUnavailable("DOWNLOAD_URL_CHANGED")
                    if await download.failure():
                        raise BrowserUnavailable("DOWNLOAD_FAILED")
                    path = await download.path()
                    if path is None or Path(path).stat().st_size > MAX_BYTES:
                        raise BrowserUnavailable("DOWNLOAD_SIZE_INVALID")
                    raw = Path(path).read_bytes()
                    return dict(raw=raw, filename=download.suggested_filename, source_url=download.url,
                                http_status=statuses.get(download.url), discovery_url=page.url,
                                discovery_body=snapshot)
                finally:
                    await asyncio.wait_for(context.close(), timeout=5)
            finally:
                await asyncio.wait_for(browser.close(), timeout=5)
    try:
        # Bounds the entire session, including download completion (not just start).
        return asyncio.run(asyncio.wait_for(capture(), timeout=90))
    except (PlaywrightTimeout, TimeoutError) as exc:
        raise BrowserUnavailable("BROWSER_TIMEOUT") from exc
    except PlaywrightError as exc:
        # Do not persist browser errors containing request headers/cookies.
        raise BrowserUnavailable("BROWSER_UNAVAILABLE") from exc


def _immutable(path: Path, raw: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("RETAINED_SOURCE_HASH_MISMATCH")
    else:
        with path.open("xb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())


def _json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


class EvidenceEngine:
    def __init__(self, root: Path, staging: Path | None = None):
        self.root = Path(root).resolve()
        self.staging = Path(staging).resolve() if staging is not None else self.root / "staging"
        self.ledger = self.root / "evidence_ledger.json"

    def _verify_entry(self, entry: dict) -> dict:
        for field in ("sha256", "entry_id"):
            if not re.fullmatch(r"[0-9a-f]{64}", entry[field]):
                raise ValueError("LEDGER_REFERENCE_INVALID")
        raw_path = "raw/" + entry["sha256"] + ".bin"
        inventory_path = "inventories/" + entry["entry_id"] + ".json"
        if entry["raw_path"] != raw_path or entry["inventory_path"] != inventory_path:
            raise ValueError("LEDGER_REFERENCE_INVALID")
        for path in (raw_path, inventory_path):
            if not (self.root / path).resolve().is_relative_to(self.root):
                raise ValueError("LEDGER_REFERENCE_INVALID")
        if digest((self.root / raw_path).read_bytes()) != entry["sha256"]:
            raise ValueError("RETAINED_SOURCE_HASH_MISMATCH")
        data = (self.root / inventory_path).read_bytes()
        if digest(data) != entry["inventory_sha256"]:
            raise ValueError("RETAINED_INVENTORY_HASH_MISMATCH")
        return json.loads(data)

    @contextmanager
    def _lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".ledger.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _commit(self, ledger: dict):
        with tempfile.NamedTemporaryFile(dir=self.root, prefix=".ledger-", delete=False) as output:
            pending = Path(output.name)
            try:
                output.write(_json_bytes(ledger))
                output.flush()
                os.fsync(output.fileno())
                os.replace(pending, self.ledger)
                directory = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if pending.exists():
                    pending.unlink()

    def status(self) -> dict:
        from .staging_status import coverage_status
        ledger: dict = json.loads(self.ledger.read_text()) if self.ledger.exists() else dict(schema_version=SCHEMA, entries=[])
        if ledger.get("schema_version") != SCHEMA:
            raise ValueError("LEDGER_SCHEMA_UNSUPPORTED")
        # Retained MCX artifacts cannot create readiness blockers, even if absent locally.
        inventories = [(entry, self._verify_entry(entry)) for entry in ledger["entries"]
                       if not calendar_disabled(entry.get("exchange", ""))]
        return coverage_status(inventories, EXCHANGES)

    def inspect(self, staged: Path) -> dict:
        """Read-only ingestion preview, including checksum/schema checks."""
        return self.ingest(staged, dry_run=True)

    def ingest(self, staged: Path, *, dry_run=False, acquisition_route="MANUAL") -> dict:
        if acquisition_route not in {"MANUAL", "BROWSER"}:
            raise ValueError("ACQUISITION_ROUTE_INVALID")
        path = Path(staged).resolve()
        if not path.is_relative_to(self.staging.resolve()) or Path(staged).is_symlink():
            raise ValueError("STAGING_PATH_REQUIRED")
        sidecar = path.with_name(path.name + ".json")
        if sidecar.is_symlink():
            raise ValueError("STAGING_PATH_REQUIRED")
        if not sidecar.exists():
            raise ValueError("SOURCE_METADATA_REQUIRED")
        if path.stat().st_size > MAX_BYTES or sidecar.stat().st_size > 100000:
            raise ValueError("SOURCE_TOO_LARGE")
        meta = json.loads(sidecar.read_text())
        return self._ingest(path.read_bytes(), path.name, meta, acquisition_route, dry_run=dry_run)

    def _ingest(self, raw: bytes, filename: str, meta: dict, route: str, *, dry_run=False) -> dict:
        require_calendar_exchange(meta.get("exchange", ""))
        if len(raw) > MAX_BYTES or not raw:
            raise ValueError("SOURCE_SIZE_INVALID")
        requested = date.fromisoformat(meta["trading_date"]) if meta.get("trading_date") else None
        if requested is None:
            try:
                identify_filename(filename)
            except ValueError:
                requested = trading_date_from_rows(raw, (meta.get("field_map") or {}).get("trading_date"))
        venue, trading_date = identify_filename(filename, requested)
        exchange = meta.get("exchange") or venue
        require_calendar_exchange(exchange)
        if exchange not in ROOT_PAGES:
            raise ValueError("EXCHANGE_REQUIRED")
        venue, _ = identify_filename(filename, trading_date)
        if venue and venue != exchange:
            raise ValueError("FILENAME_EXCHANGE_MISMATCH")
        url = guarded_url(meta["provenance_url"], exchange)
        sha = digest(raw)
        if meta.get("sha256") and meta["sha256"] != sha:
            raise ValueError("SOURCE_HASH_MISMATCH")
        if not meta.get("segment") or not meta.get("report_name"):
            raise ValueError("REPORT_PROVENANCE_REQUIRED")
        error = None
        fields = None
        try:
            payload = table_bytes(raw)
            fields = FieldMap(**meta["field_map"]) if meta.get("field_map") else detect_fields(payload, exchange, trading_date)
            inventory = parse_staged_eod(payload, exchange=exchange, segment=meta["segment"],
                                        trading_date=trading_date, provenance_url=url, fields=fields)
            inventory["source_sha256"] = sha  # Exact original archive, not the decoded table.
            inventory["table_decoder_version"] = "staged-table/v1"
            inventory.update(reconcile([inventory]))
        except (ValueError, UnicodeError, csv.Error, zipfile.BadZipFile, gzip.BadGzipFile, EOFError, zlib.error, KeyError, OverflowError) as exc:
            inventory = dict(observations=[], source_url=url, source_sha256=sha)
            # Retain a bounded reason, never raw source contents in logs/errors.
            error = str(exc) if re.fullmatch(r"[A-Z0-9_]+", str(exc)) else type(exc).__name__
        key = digest(_json_bytes([exchange, trading_date.isoformat(), sha, url, route,
                                  vars(fields) if fields else None, meta["segment"], meta["report_name"]]))
        with nullcontext() if dry_run else self._lock():
            if not dry_run:
                _immutable(self.root / "raw" / (sha + ".bin"), raw)
            ledger: dict = json.loads(self.ledger.read_text()) if self.ledger.exists() else dict(schema_version=SCHEMA, entries=[])
            if ledger.get("schema_version") != SCHEMA:
                raise ValueError("LEDGER_SCHEMA_UNSUPPORTED")
            for entry in ledger["entries"]:
                if entry["entry_id"] == key:
                    self._verify_entry(entry)
                    return entry
            LOG.info("Parsing evidence exchange=%s date=%s sha256=%s", exchange, trading_date, sha)
            # Existing parser owns dates/identity; pandas only filters already parsed rows.
            import pandas as pd
            frame = pd.DataFrame(inventory["observations"])
            allowed = {symbol for symbol, venue in EXCHANGES.items() if venue == exchange}
            if exchange == "MCX":
                allowed |= {"CRUDEOILM", "GOLDM", "SILVERM"}  # Preserve raw spellings, no historical alias claim.
            selected = frame[frame["underlying"].isin(allowed)].to_dict("records") if not frame.empty else []
            dates = sorted({row["expiry_date"] for row in selected})
            grouped = [dict(underlying=symbol, instrument=instrument,
                            expiry_dates_iso=sorted({r["expiry_date"] for r in selected
                                                     if (r["underlying"], r["instrument"]) == (symbol, instrument)}))
                       for symbol, instrument in sorted({(r["underlying"], r["instrument"]) for r in selected})]
            inventory["selected_observations"] = selected
            inventory_path = "inventories/" + key + ".json"
            if not dry_run:
                _immutable(self.root / inventory_path, _json_bytes(inventory))
            same_day = [e for e in ledger["entries"] if (e["exchange"], e["trading_date"]) == (exchange, trading_date.isoformat())]
            prior = []
            for entry in same_day:
                prior.append(self._verify_entry(entry))
            conflict = reconcile([*prior, inventory])
            entry = dict(entry_id=key, trading_date=trading_date.isoformat(), exchange=exchange,
                         sha256=sha, publisher="Exchange Official Archive", publisher_status="OPERATOR_ASSERTED" if route == "MANUAL" else "BROWSER_SOURCE_OBSERVED_NOT_REVIEWED",
                         acquisition_date=now(), extracted_expiries=[date.fromisoformat(d).strftime("%d-%b-%Y").upper() for d in dates],
                         expiry_dates_iso=dates, provenance_url=url, status=conflict["verification_status"],
                         expiry_observations=grouped,
                         acquisition_route=route, original_filename=filename, bytes=len(raw),
                         raw_path="raw/" + sha + ".bin", inventory_path=inventory_path,
                         inventory_sha256=digest(_json_bytes(inventory)), provider_listing_status="UNVERIFIED",
                         calendar_activation=False, conflicts=conflict["conflicts"],
                         reason="PARSER_REJECTED" if error else "NO_TARGET_RECORDS" if not dates else "INDEPENDENT_REVIEW_AND_CALENDAR_COMPLETENESS_REQUIRED",
                         parser_error=error, http_status=meta.get("http_status"),
                         discovery_sha256=meta.get("discovery_sha256"), segment=meta["segment"],
                         report_name=meta.get("report_name"), field_map_version=fields.version if fields else None,
                         parser_version=inventory.get("parser_version"),
                         source_metadata={k: meta[k] for k in ('exchange', 'segment', 'trading_date',
                             'provenance_url', 'report_name', 'sha256', 'field_map', 'http_status',
                             'report_page_url', 'filename', 'content_type', 'discovered_at',
                             'downloaded_at', 'discovery_sha256', 'acquisition_proof_sha256') if k in meta})
            if dry_run:
                return entry
            if error or not dates:
                # Rejected bytes remain reviewable but never enter the evidence ledger.
                rejected = self.root / "rejections" / (key + ".json")
                if not rejected.exists():
                    _immutable(rejected, _json_bytes(entry))
                return entry
            ledger["entries"].append(entry)
            self._commit(ledger)
            LOG.info("Ledger appended exchange=%s date=%s status=%s", exchange, trading_date, entry["status"])
            return entry

    def browser(self, exchange: str, trading_date: date, report_page: str, *,
                download_url: str | None = None, field_map: dict | None = None, adapter=playwright_download) -> dict:
        require_calendar_exchange(exchange)
        guarded_url(report_page, exchange)
        if download_url:
            guarded_url(download_url, exchange)
        key = digest(_json_bytes([exchange, trading_date.isoformat(), report_page, download_url]))
        folder = self.root / "browser_attempts" / key
        folder.parent.mkdir(parents=True, exist_ok=True)
        try:
            folder.mkdir()
        except FileExistsError:
            if (folder / "result.json").exists():
                cached = json.loads((folder / "result.json").read_text())
                if "entry" in cached:
                    self._verify_entry(cached["entry"])
                    if cached["entry"] not in json.loads(self.ledger.read_text())["entries"]:
                        raise ValueError("LEDGER_ENTRY_MISMATCH")
                return cached
            return dict(status="MANUAL_REQUIRED", reason="PREVIOUS_ATTEMPT_INCOMPLETE")
        try:
            capture = adapter(exchange=exchange, trading_date=trading_date, report_page=report_page, download_url=download_url)
            guarded_url(capture["source_url"], exchange)
            _immutable(folder / "discovery.html", capture["discovery_body"])
            _immutable(folder / "archive.bin", capture["raw"])
            meta = dict(exchange=exchange, segment="COM" if exchange == "MCX" else "FO",
                        trading_date=trading_date.isoformat(), provenance_url=capture["source_url"],
                        http_status=capture["http_status"], report_name=report_page,
                        discovery_sha256=digest(capture["discovery_body"]), field_map=field_map)
            entry = self._ingest(capture["raw"], capture["filename"], meta, "BROWSER")
            result = (dict(status="MANUAL_REQUIRED", reason=entry["reason"])
                      if entry["reason"] in {"PARSER_REJECTED", "NO_TARGET_RECORDS"}
                      else dict(status="INGESTED_FOR_REVIEW", entry=entry))
        except (BrowserUnavailable, ValueError, KeyError, TypeError, OSError) as exc:
            LOG.warning("Browser evidence unavailable exchange=%s date=%s error_type=%s", exchange, trading_date, type(exc).__name__)
            result = dict(status="MANUAL_REQUIRED", reason=str(exc) if isinstance(exc, BrowserUnavailable) else type(exc).__name__, acquired_at=now())
        _immutable(folder / "result.json", _json_bytes(result))
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--staging", type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--ingest", action="store_true", help="Ingest every staged archive")
    actions.add_argument("--status", action="store_true", help="Read-only 28-combination coverage matrix")
    actions.add_argument("--dry-run", action="store_true", help="Parse/check staging without any writes")
    commands = parser.add_subparsers(dest="command")
    manual = commands.add_parser("ingest", help="Read staged files with matching .json metadata; no network")
    manual.add_argument("files", nargs="*", type=Path)
    browser = commands.add_parser("browser", help="One isolated public browser attempt, then manual fallback")
    browser.add_argument("--exchange", choices=list(ROOT_PAGES), required=True)
    browser.add_argument("--date", type=date.fromisoformat, required=True)
    browser.add_argument("--report-page", required=True)
    browser.add_argument("--download-url")
    browser.add_argument("--field-map", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.command and (args.ingest or args.status or args.dry_run):
        parser.error("Use either a subcommand or an action flag")
    if not args.command and not (args.ingest or args.status or args.dry_run):
        parser.error("An action is required")
    root = args.artifacts or (args.staging.parent if args.staging else Path("artifacts"))
    engine = EvidenceEngine(root, args.staging)
    try:
        if args.status:
            print(json.dumps(engine.status(), indent=2))
            return 0
        if args.command == "ingest" or args.ingest or args.dry_run:
            paths = getattr(args, "files", None) or sorted(p for p in engine.staging.glob("*") if p.suffix.lower() in {".csv", ".zip", ".gz", ".xlsx"})
            if not paths:
                print(json.dumps(dict(status="UNVERIFIED", reason="NO_STAGED_ARCHIVES", dry_run=args.dry_run)))
                return 2
            failed = False
            for path in paths:
                try:
                    entry = engine.ingest(path, dry_run=args.dry_run)
                    rejected = entry["reason"] in {"PARSER_REJECTED", "NO_TARGET_RECORDS"}
                    failed |= rejected
                    print(json.dumps(dict(original_filename=path.name, sha256=entry["sha256"],
                        status=entry["status"], reason=entry["reason"], parser_error=entry.get("parser_error"),
                        exchange=entry["exchange"], trading_date=entry["trading_date"],
                        expiry_dates_iso=entry["expiry_dates_iso"], dry_run=args.dry_run)))
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    code = str(exc) if re.fullmatch(r"[A-Z0-9_]+", str(exc)) else type(exc).__name__
                    if code == "CALENDAR_EXCHANGE_DISABLED":
                        # Excluded retained files must not fail an active-exchange batch.
                        print(json.dumps(dict(original_filename=path.name, status="DISABLED",
                            scope="OUT_OF_SCOPE", reason=code, dry_run=args.dry_run)))
                    else:
                        failed = True
                        print(json.dumps(dict(original_filename=path.name, status="UNVERIFIED", reason=code, dry_run=args.dry_run)))
            return 2 if failed else 0
        result = engine.browser(args.exchange, args.date, args.report_page, download_url=args.download_url,
                                field_map=json.loads(args.field_map.read_text()) if args.field_map else None)
        print(json.dumps(dict(status=result["status"], calendar_activation=False)))
        return 0 if result["status"] == "INGESTED_FOR_REVIEW" else 2
    except (ValueError, KeyError, TypeError, OSError) as exc:
        code = str(exc) if re.fullmatch(r"[A-Z0-9_]+", str(exc)) else type(exc).__name__
        LOG.error("Evidence ingestion failed code=%s", code)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
