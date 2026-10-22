# NSE official data ingestion contract

This opt-in module (`app.nse_official`) consumes the existing official acquisition
inventory. It does not alter existing live engines, broker adapters, schedulers,
database schemas, provider calendars or research snapshots.

## Evidence and supported scope

The registry verifies the raw bytes, sidecars and discovery-parent hashes in
`calendar_evidence_acquisition/parsed/nse_market_data_20261003.json` (which retains
the earlier `nse_page_discovery_20261003.json` sources without modifying them). It accepts
only independently observed official NSE sources with successful responses,
policy evidence and no redirects. Integrity is distinct from independent calendar
review. The registry is trusted repository configuration, not an API input.

The following exact endpoints were retained by the acquisition workflow and are
used without URL construction:

- `https://www.nseindia.com/api/historicalOR/foCPV?from=01-10-2026&to=01-10-2026&instrumentType=OPTIDX&symbol=NIFTY&year=2026&expiryDate=06-OCT-2026&optionType=CE`
- `https://www.nseindia.com/api/historicalOR/meta/foCPV/expireDts?instrument=OPTIDX&symbol=NIFTY&year=2026`
- `https://www.nseindia.com/api/daily-reports?key=FO`
- `https://www.nseindia.com/api/option-chain-contract-info?symbol=NIFTY`
- `https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol=NIFTY&expiry=06-Oct-2026`

Historical parsing is validated against the retained 70-row NIFTY OPTIDX CE
response for 2026-10-01 and the explicit NSE expiry 2026-10-06. Other requested
dates, symbols and endpoints require their own captured discovery evidence.
There are no URL templates. The selector contains 50 observed expiry dates; its
presence does not establish when contracts were listed or a historical cutoff.

The option-chain page, its exact published script, the current selector and chain
were successfully acquired over verified HTTPS/HTTP/1.1. The requests follow the
official JavaScript, the page's selected NIFTY value, and the actual returned
`expiryDates[0]`; no date or archive URL was inferred. Original responses and
sidecars are in `artifacts/staging/nse-market-data-20261003/`. Their independent
observation does not upgrade calendar/provider-listing status.

`fetch_nse_option_chain` validates the captured schema, both legs, expiry/strike,
underlying, finite numbers, duplicate contracts, filtered-record parity and OI/
volume totals. The retained response has 116 strike rows, nine placeholder legs
with missing identities, and source timestamp `01-Oct-2026 15:40:00`. It therefore
returns `DATA_UNAVAILABLE / INCOMPLETE_CHAIN_CONTRACT`, with freshness `STALE`,
and publishes no normalized subset. Its raw bytes remain intact. A complete but
stale response also remains unavailable. The parser is not the legacy adapter.

NSE's captured `option-chain-v3.js` line 1143 explicitly renders the timestamp
with `IST`. Its hash is retained as timezone evidence; without it the registry
rejects chain interpretation. The current selector has 18 observed expiry dates,
including later years; these are observations at capture time, not historical
listing evidence. `year=None` selects this source; an explicit year selects the
independently observed historical selector. IV is preserved as reported with
unverified units; OHLC, tokens and lot sizes absent from the chain remain null.
Zero/nonpositive quote prices cannot qualify as live quotes. The chain's timestamp
is the report snapshot time, not the last-trade time of every contract.

Daily report metadata contains separate path and
filename fields. `fetch_nse_bhavcopy_metadata` preserves that metadata but does not
join those fields into an unobserved download URL.

## Pipeline and identity

Official acquisition inventory → adapter → immutable raw bytes and receipt →
whole-response normalization/validation → atomic normalized dataset and observed
contracts → explicit consumer API. Brokers enter only through the independent
source-selection/comparison helpers, never the official store.

Dataset identity is source + NSE + symbol + instrument + trade date + expiry +
option side. Each dataset contains unique strikes. Duplicate contract rows,
missing required fields, inconsistent timestamps, malformed/non-finite numbers,
partial-response markers and supplied row-count mismatches reject the entire
response. No malformed rows are dropped to obtain a valid subset.

`expected_row_count` can enforce an independently known count. Without an official
contract-universe manifest, validation proves the exact response only; it cannot
prove that the exchange returned every possible strike. The envelope explicitly
sets `contract_universe_complete=false`. This is not a complete security master.
Token is unavailable; lot size is retained only if supplied, and missing optional
master fields remain null. No production calendar can be enabled from this subset.

Raw bytes are stored before JSON parsing under their SHA256. Receipts bind exact
endpoint, request parameters, HTTP status, fetch time and discovery evidence.
Normalized data is checked against raw bytes and receipts again during ingestion
and reads. Atomic publication uses a local filesystem lock and immutable files;
an identical dataset is `ALREADY_INGESTED`. A changed response for the same identity
is `DATASET_REVISION_CONFLICT`, retains the new raw evidence and never overwrites
the published dataset. Revision approval is a separate future workflow.

## Time, prices and source selection

`FH_TIMESTAMP` supplies a trading date, not an intraday quote time.
`FH_TIMESTAMP_ORDER` is checked as an India-time date-sort key and preserved raw;
it is not promoted to a quote timestamp. `data_timestamp` is the session date;
`source_timestamp` and live `data_age_seconds` remain unavailable. Fetch/received
and processing timestamps are distinct. Reprocessing retained data keeps its
original fetch timestamp.

The retained response includes 16 zero-trade contracts with zero O/H/L/LTP but
nonzero reported settlement/close. Those values remain as published, labelled
`NO_TRADES_REPORTED`; they are not live prices. Traded rows receive OHLC range
validation. Quantities retain `NSE_REPORTED_QUANTITY`; no contracts/lots conversion
is guessed. Comparisons require matching explicit units for volume/OI.

`freshness` requires actual aware timestamps. LIVE (default ≤2s), RECENT (≤60s),
STALE and DATA_UNAVAILABLE are distinct; thresholds are configurable. Future or
missing source time cannot be LIVE. Age measures receipt latency, not the current
age of a cached quote. Live source selection also checks current age (or explicit
`as_of`) and excludes observations received after that time.

`select_source` defaults to NSE only. Explicitly enabling fallback selects one
whole DHAN result, then ANGELONE; every row is labelled FALLBACK. An explicit
provider never switches. Known request identity must match across providers.
Consumers must provide validated, identically scoped broker responses. This helper
does not authenticate external broker data or integrate it into existing engines.

`compare_sources` never mutates either input. Identity/time differences are
reported separately; quantities with unknown or mismatched units stay unavailable.
Daily aggregate comparisons are not simultaneous live-quote comparisons. Price/OI
differences are `DATA_DISCREPANCY`, not evidence of manipulation.

## Optional transport and operation

Default reads use retained official observations, with no network access.
`OfficialHTTPTransport` is opt-in. It fetches current robots policy, retains it,
then issues one GET to the exact registered URL using verified TLS and HTTP/1.1.
It rejects redirects, credentials, unregistered URLs, oversized/truncated bodies
and policy denial. It uses no browser spoofing, environment proxies, cookies,
automatic retries, broker APIs or order endpoints. Five public NSE GETs (robots,
page, script, selector and chain) succeeded during acquisition. Sandbox DNS/network
failures were retried outside the sandbox only with tool approval. No exchange
denial was overridden; no browser, cookies or authentication were used. Unit tests
remain fully offline. The live response is stale/incomplete, not a successful
live-feed validation.

From the repository root, validate in a temporary store:

```sh
PYTHONPATH=backend backend/venv/bin/python -B -m app.nse_official \
  --repository . \
  --inventory backend/calendar_evidence_acquisition/parsed/nse_market_data_20261003.json \
  --symbol NIFTY --instrument-type OPTIDX --trade-date 2026-10-01 \
  --expiry 2026-10-06 --option-type CE --expected-row-count 70
```

Add `--ingest --output /explicit/nonproduction/store` to retain a normalized
dataset. `--live` explicitly opts into the policy-checked exact URL refresh; no
live access is needed to validate the retained response. Store directories must
be operator-controlled local filesystems, not untrusted symlink/shared uploads.

## Activation gate

Expiry observations are cached as `OBSERVED_NOT_CALENDAR_VERIFIED`, separately
from production calendars. AUTO/NEXT remain fail-closed. Existing production
calendar policy still requires complete reviewed evidence, provider listing and
publication applicability, cutoff and historical coverage. A single response,
annual selector or valid hash cannot satisfy that gate. No installation or
automatic migration path is added here.
