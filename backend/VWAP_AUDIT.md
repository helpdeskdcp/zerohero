# NIFTY VWAP Audit — "VWAP: —" on NIFTY, present on NATURALGAS

**Date:** 2026-09-01 · **Commit:** `e619e08` · Read-only trace first, then the smallest evidence-backed fix.

---

## A. Root cause

**NIFTY is tracked as the NSE *cash index* (token `99926000`, `AMXIDX`). An index is a
computed number, not a traded instrument — its OHLC candles carry `volume = 0`.**

VWAP is *volume*-weighted:
```
_vwap(H,L,C,V,n) -> Σ(typical_price · vol) / Σ vol   ,  None if Σ vol <= 0
compute_sr:  vwap = _vwap(...) if any(V) else None
```
NIFTY's `V` is all-zero → `any(V)` is `False` → `vwap = None`. **Every time.**

**NATURALGAS / CRUDEOIL are MCX *futures* (`FUTCOM`)** with real traded volume in their candles,
so once the aggregator is seeded from broker candles their `V` is > 0 and VWAP computes.

### The complete data path traced

| stage | NIFTY | NATURALGAS |
|---|---|---|
| `_underlying_ref()` | NSE index token `99926000` | MCX front-month FUTCOM token |
| WS tick feed (`_pump_feed`) | `agg.add_tick(now, ltp)` — **no volume arg** (0) | same — **no volume** |
| aggregator seed (`seed_from_ohlc`) | broker 1-m candles → **`v = 0` on every bar** | broker 1-m candles → **`v > 0`** |
| `compute_sr` | `any(V)` = `False` → `vwap = None` | `any(V)` = `True` → `vwap` computed |
| snapshot persist | `vwap` column = `NULL` | `vwap` = number |
| API `/api/autoscalp/snapshots` | `vwap: null` | `vwap: 273.63` |
| frontend Auto-Scalp strip | `fmt(null,1)` → `"—"` | `"273.6"` |

The **only** source of volume in an autoscalp aggregator is `seed_from_ohlc` (live ticks add 0).
So VWAP can only ever be non-null when the aggregator was seeded from broker candles that carried
volume — which an index never does.

### Which of A–G it is → **A. volume is missing** (specifically: structurally zero for an index)

- **not B** (bars) — NIFTY has ATR, support, resistance from the same bars; only VWAP is missing.
- **not C** — the calc runs; it correctly returns `None` for zero volume.
- **not D** — the NIFTY index token / NSE mapping is correct; that *is* why there's no volume.
- **not E** — session reset is per-aggregator and identical for all symbols.
- **not F** — serialization keeps `null`; the API always carried the `vwap` key.
- **not G** — the frontend faithfully renders `null` → `"—"`.

## B. NIFTY data availability

- **Price:** ✅ live (WS mark `99926000`, age < 1 s).
- **ATR / support / resistance / regime:** ✅ computed from OHLC.
- **Volume:** ❌ **zero** — confirmed with live broker data: `fetch_candles("NSE","NIFTY", …, "1m")`
  returned 47 candles, **every one `volume = 0.0`**, total 0.
- **Historical:** `SELECT COUNT(vwap) FROM live_market_snapshots WHERE symbol='NIFTY'` → **0 non-null
  out of 2650**. NIFTY VWAP has never been computable.

**Verdict: NIFTY index VWAP is legitimately unavailable.** A real VWAP for it cannot exist without
volume, and fabricating one (e.g. from option volume or a futures proxy) would be a different
number with different meaning — the brief explicitly forbids that.

## C. Before / after behaviour

| | before | after |
|---|---|---|
| NIFTY `vwap` | `null` | `null` (unchanged — never faked) |
| NIFTY reason | none — silent `"—"` | `vwap_status: "invalid_volume"`, `vwap_reason: "bar series carries no traded volume — an NSE cash index has no volume…"` |
| NATURALGAS `vwap` | `273.63` | `273.63` (identical — regression-tested) |
| NATURALGAS status | none | `vwap_status: "available"` |
| < 12 bars | `DATA_UNAVAILABLE` | same + `vwap_status: "insufficient_data"` |
| frontend NIFTY VWAP cell | `—` | `— n/a (no volume)` + tooltip |
| API contract | `vwap` key | `vwap` unchanged **+** `vwap_status` (new, nullable) |

## D. Exact code change

- **`app/engines/sr_engine.py` `compute_sr()`** — after computing `V`:
  - `any(V) == False` → `vwap = None`, `vwap_status = "invalid_volume"`, explanatory `vwap_reason`.
  - `sum(V) <= 0` → same status, reason "total bar volume <= 0".
  - else → `vwap = _vwap(...)`, `vwap_status = "available"`, `vwap_reason = ""`.
  - `n < 12` early return → `vwap_status = "insufficient_data"`.
  - `vwap` itself is **unchanged** (a number or `None`). `vwap_status` / `vwap_reason` added to the
    top-level return and to `sr_diag`.
- **`app/engines/scalp_strategy.py` `decide_from_context()`** — `vwap_status` / `vwap_reason` added
  to the NO_TRADE `ctx` and the BUY return dict.
- **`app/db.py`** — `_MIGRATIONS["live_market_snapshots"]["vwap_status"] = "TEXT"` (idempotent
  `ADD COLUMN` on boot, the pattern already used for `risk_ref` / `ev` / `rr`); `"vwap_status"`
  added to `_LIVE_SNAP_COLS`.
- **`app/autoscalp/runner.py` `_persist_snapshot()`** — writes `sig.get("vwap_status")`.
- **`frontend/static/js/app.js`** — the `#asVwap` cell shows `— n/a (no volume)` /
  `— warming up` (per `vwap_status`) with a `title` tooltip when `vwap` is `null`. **No value is
  hard-coded**; when the backend sends a number it is shown verbatim.

## E. Tests added / passed

`tests/test_sr_engine.py` (14 → 19):

| test | asserts |
|---|---|
| `test_vwap_unavailable_for_zero_volume_series_with_reason` | zero-volume series → `vwap None`, `status invalid_volume`, reason mentions volume, `vwap` family absent, `vwap_prox` component 0 |
| `test_vwap_available_when_volume_present_unchanged` | volume series → `vwap` a number, `status available`, `reason ""` |
| `test_vwap_status_insufficient_data` | < 12 bars → `DATA_UNAVAILABLE` + `vwap_status insufficient_data` |
| `test_vwap_naturalgas_regression_value_stable_across_the_change` | NG snapshot chain + fixed tape → `vwap` deterministic, `available`, `support_strength` unchanged |
| `test_vwap_status_present_on_every_ok_return` | every OK return carries `vwap_status` ∈ the enum + `vwap_reason` |

**Full suite: 288 backend passed** (was 283). Frontend: `live_monitor.test.js` + `render_smoke.test.js` pass. `compileall` clean.

## F. NATURALGAS regression result

**No change.** `test_vwap_naturalgas_regression_value_stable_across_the_change` proves the NG VWAP is
still a deterministic number with `vwap_status: "available"`, and `support_strength` is byte-stable
run-to-run. The zero-volume branch is only reachable when `any(V)` is `False`, which never happens
for a futures bar series.

## G. Safety status

| control | state |
|---|---|
| PAPER-only | ✅ `paper_mode: true` |
| LIVE trading | ✅ `live_trading: false`, `live_trading_disabled: true` |
| broker execution code | untouched |
| risk limits / safeguards | untouched |
| kill switch | present, inactive, `MONITOR` — untouched |
| trading signals | untouched (the change only *reports* why VWAP is missing; it does not alter any decision — the `vwap` value the strategy uses is identical) |
| tests | 288 backend + 2 frontend pass |
| evidence collectors | uninterrupted |

## Live confirmation status

- **API contract:** ✅ `/api/autoscalp/snapshots` now returns the `vwap_status` key (verified live).
- **Engine on live NIFTF candle shape:** ✅ 47 zero-volume candles → `vwap None`,
  `vwap_status "invalid_volume"`, reason exposed (verified).
- **Persisted `vwap_status` on a *new* snapshot:** ⏳ pending — the aggregators are re-seeding after
  today's restarts (broker candle REST has been flaky; NIFTY 0 / NG 12 / CRUDE 12 of the 20 bars
  the engine needs before it writes a snapshot). A background watcher will capture the first
  post-fix snapshot's `vwap_status` for NG (`available`) and NIFTY (`invalid_volume`).

## If a NIFTY VWAP is actually wanted (out of scope — evidence-gated)

The architecture would have to track NIFTY via the **front-month NIFTY future** (which has real
volume) instead of the cash index. That changes what "NIFTY price / ATR / S/R" mean throughout the
engine and could shift strategy behaviour, so it is **not** a safe drop-in — it needs a deliberate
design decision and PAPER validation. Not done here.

---

## Live confirmation — 2026-09-02 session (front-month index-future VWAP)

Ran with `index_vwap_from_future=True` for the full 2026-09-02 NSE session.
On open rows (`symbol='NIFTY'`, `regime != 'MARKET_CLOSED'`):

| metric | value |
|---|---|
| total open NIFTY rows | 594 |
| `vwap IS NOT NULL` AND `vwap_status='available'` | **424 (71.4 %)** |
| `vwap_status='invalid_volume'` | 170 (28.6 %) |
| avg `vwap − index_ltp` (basis) on available rows | **+126.3 pts** (range +88 … +172) |
| NG / CRUDE regression guard | 1675/1675 `available` each — no regression |

**Verdict: PASS.**
- Pre-fix baseline: NIFTY VWAP was **0 %** `available` (cash index token, volume 0
  → always `invalid_volume`). Post-fix **71.4 %** `available`.
- The **+126 pt** average premium of VWAP over the cash `index_ltp` confirms the
  VWAP is sourced from the **front-month NIFTY future** (which carries a basis),
  not the cash index (a cash-index VWAP would sit ≈ spot and read `invalid_volume`).
- The `vwap` value the strategy consumes is unchanged in meaning — this only
  *fills* a field that was permanently `None`; it is display/context only and is
  **not** fed into `compute_sr` (S/R still uses the cash-index series).

**Caveats (not regressions):**
1. NIFTF snapshots on 2026-09-02 did not begin until ~10:20 IST — the morning
   `oi-dashboard` restart re-triggered the NIFTY trading-aggregator re-seed
   (REMEDIATION_PLAN P0-2). No NIFTY eval rows exist for 09:15–10:20 IST.
2. Intraday `invalid_volume` fraction falls through the session (≈42 % → ≈19 %)
   as the front-month-future 1 m candle cache warms and occasional REST
   `insufficient_data` responses recover on the next cycle — it degrades
   gracefully to `invalid_volume`, never to a wrong number.
3. The reason string *"from front-month index future (cash index has no volume)"*
   is set on the in-memory signal dict but not persisted to a column; sourcing is
   evidenced here by the +126 pt basis and the 0 % → 71 % availability flip.
