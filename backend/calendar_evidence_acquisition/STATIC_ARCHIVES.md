# Offline static archive workflow

This tool does not install production calendars. No broker API or token is used.

1. Consult source_register.json and cache/ before acquiring anything. Import retained responses with SourceCache.retain; do not retry recorded failures. An explicit public GET transport can be passed to acquire; it must not follow redirects or call broker APIs. The module has no default transport.
2. Retain raw original bytes, URL, timestamp, HTTP status and SHA256. An append-only URL directory prevents duplicate attempts. For new versions at the same URL, use a separately declared acquisition package; never overwrite an old response.
3. Run parse_archive on cached CSV/GZIP/ZIP bytes with SourceSpec. For a dated contract master, supply its declared archive_date. This date is not proof of publication time. Only explicit known schemas/date formats are accepted; unknown formats remain unavailable.
4. Secondary mirrors always use SECONDARY_DATASET. For descriptions supplying missing identity, pass secondary_identity explicitly and retain the description. Quarantine is opt-in and preserves rejected row numbers/reasons and original bytes.
5. observed_expiries returns exact-date, source-labelled exchange candidates only. It is not historical AUTO/NEXT; an EOD observation cannot prove intraday publication. Provider-constrained requests remain unavailable.
6. holiday_candidate uses only supplied complete sessions, effective interval and rule/holiday references. It emits DERIVED_RULE/UNVERIFIED; no default calendar or universal weekday rule is provided.
7. Review evidence independently. The existing ProviderCalendars resolver still requires its full reviewed provider evidence contract; never relabel exchange data with a provider to satisfy it.

Example (from backend/, no network):

```python
from datetime import date
from pathlib import Path
from calendar_evidence_acquisition.static_archives import SourceSpec, parse_archive, observed_expiries
# raw_csv must be a locally retained, hash-checked source; classify its actual origin.
spec = SourceSpec(source_url, "SECONDARY_DATASET", "NSE", "FO_BHAVCOPY")
inventory = parse_archive(Path(raw_csv).read_bytes(), spec)
result = observed_expiries([inventory], "NSE", "NIFTY", "OPTION", date(2026, 8, 13))
```

The source archive cache and parsed inventories are evidence artifacts, never a production calendar. Tests use synthetic format fixtures and temporary storage, with all network/secret access blocked. Actual downloaded secondary inventories are additionally reparsed under the same isolated test runner.
