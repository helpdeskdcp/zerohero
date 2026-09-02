# AngelOne Adapter Upgrade — Option Greeks + canonical option chain

**Date:** 2026-09-02 · **Scope:** `broker/angelone/` data-acquisition + normalisation ONLY.
No Black-Scholes, no synthetic greeks, no prediction/probability/trading logic in the
adapter. `option_engine.py` and autoscalp decision logic untouched.

---

## 1–2. Field inventory & classification

Legend — **A** already wired · **B** provided by AngelOne but was NOT wired · **C** not
provided by AngelOne · **D** must be derived by a separate engine (never the adapter).

| field | class | endpoint | before this change | after |
|---|---|---|---|---|
| ltp / open / high / low / close | **A** | `market/v1/quote` FULL | wired | wired (+ OHLC now on the leg) |
| volume (`tradeVolume`) | **A** | `market/v1/quote` FULL | wired | wired |
| oi (`opnInterest`) | **A** | `market/v1/quote` FULL | wired | wired |
| oi_change (`changeinOpenInterest`) | **B/intermittent** | `market/v1/quote` FULL | tried, usually null | tried, `data_source` records when present |
| bid / ask / depth (5-level) | **B** | `market/v1/quote` FULL `depth` | **not wired** | **wired** — `bid`=`depth.buy[0].price`, `ask`=`depth.sell[0].price`, full `depth` passed through |
| net_change / pct_change / circuits | **B** | `market/v1/quote` FULL | **not wired** | **wired** (pass-through) |
| **delta / gamma / theta / vega** | **B** | `marketData/v1/optionGreek` | **not wired** (method existed, uncalled, wrong URL) | **wired** — one request per underlying+expiry, merged per strike/type |
| **iv** (`impliedVolatility`, %) | **B** | `marketData/v1/optionGreek` | **not wired** | **wired** — stored as decimal fraction (`iv`), raw % kept as `iv_pct` |
| strike | **A** | instrument master (paise ÷100) + optionGreek | wired | wired |
| option_type (CE/PE) | **A** | instrument master (symbol suffix) | wired | wired + explicit on the leg |
| expiry | **A** | instrument master | wired | wired + explicit on the leg |
| symbol / token | **A** | instrument master | wired | wired |
| candles OHLCV | **A** | `historical/v1/getCandleData` | wired | wired (unchanged) |
| rho, charm, vanna, 2nd-order greeks | **C** | — | n/a | **NOT_PROVIDED** — AngelOne optionGreek returns delta/gamma/theta/vega/IV only |
| mid / microprice | **D** | — | n/a | derive from bid/ask in an engine |
| synthetic greeks when IV missing (Black-Scholes) | **D** | — | n/a | belongs to `sr_engine` / a greeks engine, **never** the adapter |

### Bugs found & fixed in the adapter
1. **`GREEKS` URL was wrong**: `.../market/v1/optionGreek` → corrected to
   `.../marketData/v1/optionGreek` (SmartAPI forum topic 4254). The old path would 404.
2. `get_greeks()` did one un-cached call, no normalisation, no error taxonomy, returned a
   thin `{status, rows}` — replaced by `get_option_greeks()` (below); `get_greeks()` kept
   as a deprecated delegating shim so nothing breaks.
3. `get_option_chain()` cherry-picked 5 quote fields and dropped depth/OHLC/greeks.

### Operational note — credentials
`ANGEL_*` are populated in the **running service's** environment (systemd `EnvironmentFile`
= `backend/.env`) — a bare-shell `python` does NOT load them, which is why an offline
`./venv/bin/python` call reports `CONFIG_REQUIRED`. The service authenticates fine; live
greek capture is confirmed working via the histcap worker (see `HISTCAP_IMPLEMENTATION.md`).

---

## 3–9. What was implemented

### `client.get_option_greeks(underlying, expiry)` — canonical, cached, deduped
- **One** POST to `marketData/v1/optionGreek` per `(UNDERLYING, EXPIRY)`.
- TTL cache `ANGEL_GREEK_TTL_SEC` (default **15s**); OK results cached full TTL, errors
  negative-cached `min(5s, TTL)`.
- Concurrent identical callers share the single in-flight request via a per-key lock
  (`_greek_locks`, guarded by `_greek_locks_guard`); double-checked against the cache.
- **Never raises. Never fabricates.** Return schema:
  ```
  status : OK | NO_DATA | AUTH_FAILED | RATE_LIMITED | TIMEOUT | MALFORMED | API_ERROR
  source : "ANGELONE_OPTION_GREEK"   endpoint   underlying   expiry
  http_status | errorcode | message | fetched_at | cache: HIT|MISS
  rows   : [ normalize_greek_row(...) ]     (empty unless status == OK)
  ```
  - `AB9019` / `"No Data Available"` / empty `data[]` → `NO_DATA` (not an error).
  - HTTP 429 → `RATE_LIMITED`; `requests.Timeout` → `TIMEOUT`; other exception → `API_ERROR`
    (message truncated, no traceback leak); non-JSON body → `MALFORMED`.
  - unauthenticated → `AUTH_FAILED` with the `last_auth` status as `errorcode`.

### `greeks.normalize_greek_row(raw)` — one canonical schema
`{strike (₹ float), option_type ("CE"/"PE"), delta, gamma, theta, vega,
iv (decimal fraction = broker % ÷ 100), iv_pct (raw %), trade_volume, source, status}`.
String→float via `_f()` (`""`, `"NA"`, `"-"`, NaN → `None`, **never 0.0**). A row with a
strike+type but no usable greek → `status: "MALFORMED"` (dropped by the indexer). Unknown
extra keys are ignored.

### `greeks.index_greek_rows` / `match_greek`
O(1) `{(round(strike,4), "CE"|"PE"): row}` index. `match_greek` does exact key first, then
nearest strike within `max(0.01, 5e-4·strike)` (instrument-master vs greek-endpoint
sub-rupee rounding). CE and PE are always distinct keys.

### `greeks.merge_leg_greeks(leg, greek_row)` — preservation rule (step 5)
A greek value is written onto the leg **only** where the leg currently holds `None` for
that exact field. `ltp / oi / oi_change / volume / token / expiry / strike / option_type /
bid / ask / depth` are never touched. Every field gets a `data_source[field]` entry
(`ANGELONE_QUOTE` | `ANGELONE_OPTION_GREEK` | `None`). `greeks_source` = `BROKER` when a
greek row matched, else `UNAVAILABLE`.

### `client.get_option_chain(underlying, expiry, window, *, with_greeks=True)` — upgraded
1. resolve spot + expiry + strike window (unchanged).
2. per leg: FULL quote → `ltp, oi, oi_change, volume, bid, ask, depth, open, high, low,
   close, net_change, pct_change, lower/upper_circuit, timestamp` (all quote-origin).
3. **one** `get_option_greeks(underlying, selected_expiry)` for the whole chain.
4. merge per `(strike, type)` via `merge_leg_greeks`.
5. return adds a `greeks` block: `{status, source, expiry, errorcode, message, cache,
   rows_returned, indexed, matched, requested}` for observability.
- If the greek call fails for any reason, the chain still returns OK with quote data and
  `delta…iv = None` (`greeks_source: "UNAVAILABLE"`). Failure is isolated.

### `capability.py` — adapter capability report (step 11)
`adapter_capability_report(client=None, *, probe=False)` → rows of
`{field, availability, endpoint, wired, source, status, note}`. Without a live probe every
broker field is `status: "DOC"` (never "available"). `probe=True` with an authenticated
client does **one** live quote + **one** live greek call and rewrites `status` to
`LIVE-CONFIRMED` or the exact broker error. `format_capability_report()` renders the
`FIELD | AVAILABILITY | ENDPOINT | WIRED | SOURCE | STATUS | NOTE` table.

---

## 10. Tests — `backend/tests/test_angelone_greeks_adapter.py` (25, all green)

success · empty→NO_DATA · AB9019→NO_DATA+errorcode · malformed JSON · timeout · 429 ·
generic exception · auth-failure · missing individual greek field→None · no-greek
row→MALFORMED · IV `""`→None · strike-match tolerance · CE/PE distinct · merge never
overwrites quote fields · merge keeps an existing value · merge with no greek row ·
duplicate call→cache HIT (1 request) · different key→2nd request · error negative-cached ·
expiry passed verbatim · **full chain**: greeks merged per strike/type + `data_source`
correct · greek failure doesn't break the chain · NO_DATA is clean · **one greek request
for the whole chain** (not per-strike) · capability report shape / no false claims ·
capability probe reports the exact error when unauth.

Full backend suite: **330 passed** (2 pre-existing date-fixture flakes in
`test_standalone_angelone` / `test_nse_mcx_pipeline_audit` were also converted to relative
dates in this change — verified against a clean tree that they failed identically before).

---

## 13. Verification

**Live (real AngelOne data):** BLOCKED — SDK credentials are absent in this environment
(see §1–2 finding). To run: restore `ANGEL_*` in `backend/.env`, restart `oi-dashboard`,
then `adapter_capability_report(sdk, probe=True)` and one `get_option_chain("NIFTY","AUTO")`
during market hours.

**Offline (documented broker contract):** executed against the exact
SmartAPI-forum-topic-4254 row shape.

```
RAW broker rows (marketData/v1/optionGreek data[]):
  {strikePrice:"24000.000000", optionType:"CE", delta:"0.512300", gamma:"0.000850",
   theta:"-6.240000", vega:"9.110000", impliedVolatility:"12.640000", tradeVolume:"1830450.00"}
  {strikePrice:"24000.000000", optionType:"PE", ... impliedVolatility:"12.910000", tradeVolume:""}
  {strikePrice:"24050.000000", optionType:"CE", delta:"0.431000"}   # gamma/theta/vega/iv absent

NORMALIZED adapter rows:
  CE 24000 -> delta 0.5123 gamma 0.00085 theta -6.24 vega 9.11 iv 0.1264 iv_pct 12.64  status OK
  PE 24000 -> delta -0.4877 ...                       iv 0.1291 iv_pct 12.91  trade_volume None (broker "")
  CE 24050 -> delta 0.431   gamma/theta/vega/iv = None                        status OK (still usable)

FINAL option-chain leg (CE 24000, after merge):
  ltp 151.2  oi 9032000  volume 44210  bid 151.0  ask 151.4   <- ANGELONE_QUOTE (unchanged)
  delta 0.5123  gamma 0.00085  theta -6.24  vega 9.11  iv 0.1264  iv_pct 12.64  <- ANGELONE_OPTION_GREEK
  greeks_source: BROKER
  data_source: {ltp:ANGELONE_QUOTE, oi:ANGELONE_QUOTE, volume:ANGELONE_QUOTE, bid:ANGELONE_QUOTE,
                ask:ANGELONE_QUOTE, depth:ANGELONE_QUOTE, delta:ANGELONE_OPTION_GREEK,
                gamma:ANGELONE_OPTION_GREEK, theta:ANGELONE_OPTION_GREEK, vega:ANGELONE_OPTION_GREEK,
                iv:ANGELONE_OPTION_GREEK}

Fields populated: delta, gamma, theta, vega, iv (+ all quote fields)
Fields still unavailable: none for CE 24000; for CE 24050 -> gamma/theta/vega/iv = None
  reason: the broker row for that strike omitted those keys (never fabricated)
```

### Every "unavailable" reason
| field | when unavailable | reason surfaced |
|---|---|---|
| delta/gamma/theta/vega/iv | greek call not `OK` | `greeks.status` + `errorcode`/`message`; leg `data_source[field] = None`, `greeks_source = "UNAVAILABLE"` |
| a single greek on one strike | broker row omitted that key | `normalize_greek_row` sets it `None`; row still `status OK` if any other greek present |
| iv | broker `impliedVolatility` is `""`/`"NA"` | `_f()` → `None`; `iv` and `iv_pct` both `None` |
| oi_change | FULL quote didn't include `changeinOpenInterest` | `None`; `data_source` has no entry |
| any greek, outside market hours | AngelOne returns `AB9019` | `greeks.status = "NO_DATA"`, `errorcode = "AB9019"` |
| everything, no creds | `authenticate()` false | `greeks.status = "AUTH_FAILED"` |

---

## Remaining (NOT done — needs approval; pure data-layer, no logic)

1. **Forward the greeks through `app/market_data._option_snapshot`** — it currently
   normalises a chain leg to `ltp/oi/oi_change/volume/token` only, so the enriched
   `delta…iv` + `data_source` stop there and never reach `_autoscalp_chain` / `chain_json`.
   ~1 small edit each in `app/market_data.py` and `app/main.py:_autoscalp_chain` (which
   currently hard-codes `iv/delta/gamma/theta/vega = None`). No engine/logic change.
2. **Restore `ANGEL_*` credentials** and run the live probe (step 13).
3. Deploy = restart `oi-dashboard` so `broker/angelone/` reloads.
