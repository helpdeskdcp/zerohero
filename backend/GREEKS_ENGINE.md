# Option Greeks Engine — NIFTY

**Date:** 2026-09-02 · Derived exposure metrics over **captured** AngelOne broker Greeks.
No broker fetch of its own, no fabrication, no Black-Scholes, no order path, no
trading/signal-logic change.

---

## 1. Audit — where optionGreek is fetched / stored today

| concern | current state (before this engine) |
|---|---|
| **fetch** | `broker/angelone/client.py:get_option_greeks(underlying, expiry)` — the single fetch point. POSTs `marketData/v1/optionGreek` with the full auth headers, 15 s TTL cache, per-key lock. Canonical return: `status ∈ OK\|NO_DATA\|AUTH_FAILED\|RATE_LIMITED\|TIMEOUT\|MALFORMED\|API_ERROR`, `rows:[normalize_greek_row]`. `AB9019` / "No Data" / empty → `NO_DATA`. Called from `client.get_option_chain()` and `app/histcap/worker._capture_greeks()`. |
| **normalise** | `broker/angelone/greeks.normalize_greek_row` — strings → floats, `iv` = broker % ÷ 100 (raw % kept as `iv_pct`), missing → `None` (never 0). |
| **store (raw)** | `app/histcap` → `raw_responses` (gzip, SHA-256 dedup), FK'd from every normalized row. |
| **store (normalized)** | `app/histcap` → `option_greeks` table: `received_ts, server_ts, snap_key, underlying, expiry, strike, option_type, session_date_ist, delta, gamma, theta, vega, iv, iv_pct, trade_volume, broker_status, source, raw_id, flags, run_id`. `UNIQUE(underlying, expiry, strike, option_type, snap_key)`, append-only `INSERT OR IGNORE`. Integrity flags via `histcap/integrity.greek_check` (`iv_oob` etc. — never clamped). |
| **OI** | `app/histcap` → `quote_snapshots` (kind=`OPTION`) — LTP/OI/OI-change/bid/ask/5-level depth per strike, same `received_ts` as the Greek pull (verified: identical timestamp per cycle). |
| **read** | `store.get_greeks()` + `GET /api/histcap/greeks` with `as_of` (look-ahead-safe). |
| **live volume** | ~43 k OK NIFTY Greek rows on 2026-09-02, 83 strikes/pull, ~365 pulls; ~34 k option quote snapshots. |

**Gap found:** everything up to *storage* exists. What was missing is **requirement 9/10** —
derived exposure aggregates (`OI × Greek`, weighted IV, concentration, CE/PE totals &
differences) kept **separate** from the raw broker Greeks. This engine adds exactly that.

**Compatibility verified:** `option_greeks` and `quote_snapshots` share the exact
`received_ts` per capture cycle → an exact join on `(underlying/symbol, expiry, strike,
option_type, received_ts)`. In the ATM ± 15 band, 57/59 (strike, side) pairs had both a
Greek and an OI on the last live cycle (96.6 % coverage).

---

## 2. Architecture (minimal — reuses everything)

`app/greeks_engine/` — a read-only compute layer:

| file | role |
|---|---|
| `model.py` | `Quality` enum (`VALID\|STALE\|PARTIAL\|INVALID\|NO_DATA`), derived-record column tuples, `SOURCE = "DERIVED_FROM_ANGELONE_OPTION_GREEK"` |
| `schema.py` | `greek_exposure` + `greek_engine_runs` tables (in the **same** `market_history.db`, **separate** tables). Append-only; `UNIQUE(underlying, expiry, as_of_ts)` |
| `compute.py` | pure math: `pair_exposure(oi, Δ,Γ,Θ,V)` = `OI × Greek` (None if OI or all Greeks missing), `build_snapshot(rows, …)` → one derived record + per-strike detail. No estimation. |
| `engine.py` | `GreeksEngine` — reads latest (or `as_of`) captured Greeks + OI + underlying price from the histcap store, derives, persists append-only, logs the run. `latest()`, `history(as_of=…)`, `runs()`, `status()`. |
| `api.py` | `GET /api/greeks-engine/{status,latest,exposure,runs}` (read-only) |
| `__init__.py`, `__main__.py` | exports + `python -m app.greeks_engine [--underlying NIFTY] [--expiry …] [--as-of …] [--status]` |

**Reuses:** histcap `HistStore` DB + captured rows; `broker.angelone.greeks` normalisation;
`market_calendar`; the **histcap scheduler** — `histcap/worker.run_once()` calls
`GreeksEngine().run_once("NIFTY", mode="CYCLE")` after each capture cycle that wrote Greeks
(guarded — a failure there never touches the capture). `app/main.py` mounts the router.

---

## 3. Derived metrics (requirement 9) — only from valid captured data

Per (strike `k`, side `s∈{CE,PE}`): `x_exp(k,s) = OI(k,s) · x(k,s)` for `x ∈ {Δ,Γ,Θ,V}`
(a missing `OI` or a missing Greek drops that pair — never estimated).

| metric | definition |
|---|---|
| `ce_<g>_exp` / `pe_<g>_exp` | `Σ_k x_exp(k, CE)` / `Σ_k x_exp(k, PE)` |
| `net_<g>_exp` | `ce + pe` (signed sum — PE Δ is negative) |
| `diff_<g>_exp` | `ce − pe` (magnitude difference) |
| `oi_weighted_iv` | `Σ(iv·OI) / Σ OI` over all valid legs |
| `vega_weighted_iv` | `Σ(iv·|V|·OI) / Σ(|V|·OI)` |
| `gamma_conc_strike` | strike with the largest `Σ_s |Γ_exp(k,s)|` |
| `gamma_conc_pct` | that strike's share of total `|Γ_exp|` |
| `gamma_herfindahl` | `Σ_k (share_k)²` (1 = one strike, →0 = spread) |
| `ce_oi_total` / `pe_oi_total` / `pcr_oi` | Σ OI per side, `pe/ce` |

`per_strike_json` keeps the full per-strike breakdown for backtests.

**Quality:** `NO_DATA` (no OK Greeks) · `STALE` (Greek `as_of` age > `CHANAKYA_GREEKS_STALE_SEC`,
default 90 s) · `PARTIAL` (coverage < 80 % of the band) · `INVALID` (a non-finite aggregate) ·
`VALID` (fresh + ≥ 80 % coverage). `stale_sec`, `coverage_pct`, `n_pairs_used/expected/missing`
on every row.

**Look-ahead-safe:** `history(as_of=…)` filters `as_of_ts <= as_of`, oldest first; never `id`.

---

## 4. Tests — `backend/tests/test_greeks_engine.py` (13, all green)

`pair_exposure` = OI×Greek exact / None on missing OI / None on no-Greek / partial-Greek keeps
`None` (not 0) · `build_snapshot`: totals + net(signed) + diff(CE−PE) + PCR + OI-weighted IV +
vega-weighted IV · gamma concentration (dominant strike → pct ~100, HHI ~1) · missing-OI
strikes → `PARTIAL`, totals only from strikes that had OI (no fabrication) · `NO_DATA` →
all aggregates `NULL` · `STALE` when `as_of` old · engine: derive+persist against a seeded
store, **idempotent** (2nd run = 0), `history(as_of)` look-ahead-safe, empty store →
`NO_DATA` run logged, `status()` shape.

Full backend suite: **362 passed** (was 349). `compileall` clean.

---

## 5. Live verification (real captured data, 2026-09-02 ~12:00 IST)

`python -m app.greeks_engine --underlying NIFTY` against the production `market_history.db`:

```
expiry 08SEP2026   quality VALID   coverage 96.61%   stale 10.8s
underlying_price 23954.4  (ANGELONE_QUOTE:FUTURE)
n_pairs_used 57 / expected 59   (2 band strikes had a Greek but no OI -> flagged, not filled)
net_delta_exp  -4,451,090       ce_vega_exp 960,413,519 / pe_vega_exp 730,399,853
net_gamma_exp     156,327       pcr_oi 0.785
oi_weighted_iv  0.1149          vega_weighted_iv 0.1149
gamma concentration: strike 23800, 12.75% of total |Γ_exp|, HHI 0.073 (spread, not pinned)
```
1 `greek_exposure` row written; a second run wrote 0 (append-only `UNIQUE`).

---

## 6. Still pending / notes

| item | |
|---|---|
| **deploy** | ✅ **DEPLOYED 2026-09-02 ~23:42 IST** — `oi-dashboard` restarted at commit `5555952` after NSE + MCX close (both daily reports fired). Verified post-restart: service `active`; `/api/health` 200 (`live_trading:false`, `paper_mode:true`); `/api/greeks-engine/status` returns JSON (`exposure_snapshots`, `by_quality`, `stale_sec_threshold=90`); `/api/histcap/status` `running:true`; `market_history.db` has `greek_exposure` + `greek_engine_runs`; selfcheck `last_error:null`, `config_warnings:[]`. First **live per-cycle** `greek_exposure` row lands after the 2026-09-03 09:15 IST open (engine runs per histcap cycle, market hours only); `python -m app.greeks_engine --once` works now. A market-closed restart re-triggers the NIFTY aggregator re-seed (P0-2) — NIFTY snapshots resume ~60–100 min after the 2026-09-03 open; NG/CRUDE unaffected. |
| **multiple expiries (req 8)** | the engine is expiry-agnostic (processes every expiry present in `option_greeks`). histcap currently captures only the AUTO (nearest) expiry's Greeks+OI. Capturing `current + next` is a 1-line histcap config addition (`CHANAKYA_HIST_EXPIRIES`) — not done here to keep this change minimal; propose separately. |
| **OI units** | `OI × Greek` uses AngelOne `opnInterest` as returned (contract units, not lot-adjusted). Consistent across the series; document when interpreting absolute magnitudes. |
| **rho / 2nd-order** | AngelOne `optionGreek` does not provide them → never derived. |

## 7. Safety

no order path · no trading/signal-logic change · reads histcap capture, writes its own
append-only tables · fabricates nothing (missing Greek or OI → pair dropped, row flagged) ·
Greeks sourced only from `marketData/v1/optionGreek` via the existing client · engine
failure in the worker is caught and never affects capture or trading.
