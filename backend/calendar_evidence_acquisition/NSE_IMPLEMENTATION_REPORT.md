# NSE autonomous acquisition — 2026-10-03

**IMPLEMENTED AND VALIDATED OFFLINE. LIVE ACQUISITION BLOCKED.**
Production Calendar **NOT INSTALLED**; historical AUTO/NEXT **FAIL-CLOSED**.

The final relevant suite passed **825 tests**, including **66 new tests**. The
saved Phase-2 diagnostics passed **4 tests**: **829 distinct passing cases**, zero
final failures/errors/skips/warnings. Initial test-first collection failed because
the new module did not exist; that reproduction is retained. No old test was weakened
or removed. Syntax checks passed for five files; mypy passed for four new modules
with untyped bodies checked and legacy imports excluded. Systemd unit verification
and isolated dependency checks passed. See `nse_validation_20261003/` and `validation.json`.

## Changes

| Path in this directory | Purpose |
|---|---|
| `nse_discovery.py` | Exact report/date/observed-URL contracts and page JSON interpretation |
| `nse_browser.py` | Isolated Playwright DOM/network discovery, robots policy and bounded download |
| `nse_downloader.py` | Validation, atomic publication, quarantine, provenance, engine handoff, CLI |
| `nse_job.py` | Cron-compatible latest-observed-completed-date job |
| `systemd/zerohero-nse-evidence.service` / `.timer` | Uninstalled daily job templates |
| `evidence_engine.py` | Compatible optional BROWSER ingestion route and retained acquisition metadata |
| `requirements-nse-browser.txt`, `.gitignore` | Isolated pinned dependencies and excluded browser/environment files |
| `NSE_DOWNLOADER.md`, `EVIDENCE_ENGINE.md` | Operator instructions, contracts and limitations |
| `../tests/test_nse_downloader.py` | 66 deterministic mocked cases |
| `manifest.json`, `validation.json`, snapshots/checksums/reports | Review artifacts; no production calendar data |

The browser environment is installed at `.venv/`; Chromium/FFmpeg at `.browsers/`.
Playwright 1.63.0, pandas 3.0.5 and a development-only mypy 2.4.0 were installed in
that environment. The application virtualenv, application code and existing tests
were not changed. No OS packages, production services or credentials were modified.

## Source and provenance

Starting page: `https://www.nseindia.com/all-reports-derivatives`.
Required label: `F&O - UDiFF Common Bhavcopy Final (zip)`.
URLs must be observed in the matching DOM report, an associated same-page JSON
object or a scoped official download action. There is no archive URL generator.
The label is the requested selection contract; its current live presence has not
been independently confirmed during the failed run.

Sidecars retain exchange, segment, content-validated trading date, actual observed
download URL, report-page URL, report label, observed filename, original ZIP hash,
actual HTTP/content metadata and actual observation/download timestamps. Retained
DOM/JSON and acquisition-record hashes link evidence to its observation. The engine
retains the browser source and proof hash. No cookies or headers are logged.

Successful receipt reuse verifies all retained bytes before returning
`ALREADY_VERIFIED` **for archive/acquisition only**. It never means that a Dhan or
AngelOne historical calendar is verified. Transient connection/timeouts/5xx have
three attempts maximum with 1s/2s backoff; blocked access, provenance and validation
failures stop. Different or invalid downloaded bytes are quarantined, not overwritten.

The standalone job uses the latest observed Final report dated before today in
Asia/Kolkata. It does not invent trading days. The existing trading scheduler is
not an appropriate integration point and remains unchanged. The new timer is
**not installed/enabled/started**, pending operator review and successful live access.

## Live result

The installed browser attempted the authorized October 1 discovery dry-run. Exact
result:

```json
{"calendar_status":"UNVERIFIED","detail":"NSE_CONNECTION_FAILED","production_calendar":"NOT INSTALLED","reason":"SOURCE_METADATA_REQUIRED","status":"UNVERIFIED"}
```

No live download success is claimed. No authoritative report URL was obtained,
no new exchange archive downloaded, no sidecar created and no evidence ingested.
Mocked tests passed independently of NSE availability. Network/package installation
requests were limited to public package/browser distribution and authorized NSE
discovery; no broker API or order endpoints were called.

## Existing archive and remaining limits

`artifacts/staging/BhavCopy_NSE_FO_20261001.csv.zip` remains unchanged with SHA256
`d44a052b79a3961910f74783960ebbd5043696dbed39e7fb86ec84929fd60338`.
The new validator checked its 33,250 rows and all four target indices. Its prior
download provenance is still missing. Merely observing a matching URL later does
not bind those existing bytes to that URL; the component refuses to rewrite,
redownload or silently attribute them.

Remaining risks: inaccessible official pages; unsupported date controls/DOM/JSON
layouts until actual official responses can be reviewed; Chromium needs ongoing
security maintenance; crash-interrupted publications require review when a complete
receipt is absent. Direct POST endpoints and redirects are deliberately unsupported.
All 28 provider/instrument calendars remain UNVERIFIED. Exchange EOD acquisition
alone cannot prove historical provider listings or calendar completeness.

**Next action:** review the isolated job/templates and resolve lawful access to
the public NSE report page. Run the documented dry-run after access changes; retain
actual page/network proof before acquiring new dates. For the existing October 1
archive, recover its original download evidence separately. Do not bypass access
restrictions or activate production calendars to work around the missing evidence.
