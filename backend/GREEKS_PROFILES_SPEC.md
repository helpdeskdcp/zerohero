# NSE / MCX Greeks Profiles — Spec (design only, not implemented)

**Date:** 2026-09-03 · **Status:** DESIGN — awaiting approval · **Author:** Chanakya
**Scope:** how option-Greek sourcing, staleness, and validity bands should be
segmented so **NSE index/equity F&O** and **MCX commodity options** are handled by
separate, explicit policies instead of one hard-coded NIFTY path.

This document changes **no code**. It is the reference for a later, evidence-gated
implementation. `live_trading` stays `false` throughout; nothing here adds an order
path, a broker order method, or any trading/signal-logic change.

---

## 1. Why

Today the Greeks path is written as "NIFTY, and everything else re-uses the same
call". That is wrong for MCX in three concrete ways, all verified in code / live:

| # | Fact (verified) | Consequence |
|---|---|---|
| 1 | `broker/angelone/client.py:258 get_option_greeks()` → AngelOne `marketData/v1/optionGreek` returns **`AB9019` "No Data Available"** for every MCX commodity option (NATURALGAS, CRUDEOIL). Only NSE index/equity F&O (NIFTF, BANKNIFTY…) return rows. | `app/histcap/worker.py:285 _capture_greeks()` still fires that POST **every cycle for every symbol** in `CHANAKYA_HIST_SYMBOLS` (default `NIFTY,NATURALGAS,CRUDEOIL`). Each MCX cycle = one wasted authenticated request + one `NO_DATA` error row in the run log. |
| 2 | `app/greeks_engine/engine.py` reads only `option_greeks` rows with `broker_status='OK'`. MCX has none. | `greeks_engine().run_once("MCX-symbol")` would always be `quality=NO_DATA`. The engine is also **hard-wired to `"NIFTY"`** at `worker.py:185` and in `api.py` defaults. |
| 3 | MCX IV runs structurally higher/wider than NSE index IV. Live GEX v1a: NIFTY `gex_sigma` ≈ 0.11–0.19; NATURALGAS ≈ 1.05; CRUDEOIL ≈ 1.0 (`GEX_SR_SPEC.md §11`, FLAG A). Freshness also differs — MCX ticks are sparser than NSE index. | A single `CHANAKYA_GREEKS_STALE_SEC=90` and a single implicit IV sanity band mislabel MCX data as `STALE` / out-of-band when it is in fact normal for that segment. |

The only Greek-like signal MCX has is **synthetic**: `app/engines/sr_engine.py:351
_gex_profile()` computes Black-Scholes **gamma** per strike (`_bs_gamma`, IV via
`_solve_iv` bounded bisection lo=0.03 hi=3.0, realized-vol fallback) and persists
`gex_flip / gex_pin / gex_regime_sign / gex_sigma`. It is **diagnostic-only** and
produces gamma only — no delta/theta/vega, no exposure aggregates.

**Goal of this spec:** one small policy object, `GREEKS_PROFILES`, that every Greek
touch-point consults, so NSE keeps its broker-Greek pipeline and MCX gets an
explicit synthetic-only pipeline with its own bands — no silent "re-use NIFTY".

---

## 2. What exists today (baseline, per code)

```
CHANAKYA_HIST_SYMBOLS = NIFTY,NATURALGAS,CRUDEOIL         # worker.cfg["symbols"]
_EXCH_OF (worker.py:29) = { NIFTY,BANKNIFTY,FINNIFTY -> NSE ;
                            NATURALGAS,CRUDEOIL,CRUDEOILM,GOLD,SILVER -> MCX }

per capture cycle, for each symbol with refs["opt_expiry"]:
  worker._capture_greeks()
     -> sdk.get_option_greeks(sym, expiry)          # AngelOne optionGreek POST
     -> store.put_raw(...)                          # raw always kept
     -> if status == OK: norm_greeks(...) source="ANGELONE_OPTION_GREEK"
        -> store.write_greeks() -> option_greeks table
     -> else: append {stage:"greeks", status:"NO_DATA"} to errors    # MCX path

after the cycle, if counts["greeks"] > 0 and "NIFTY" in symbols:
  greeks_engine().run_once("NIFTY", mode="CYCLE")    # NIFTY hard-coded
     -> reads option_greeks broker_status=OK + quote_snapshots OI + FUT/INDEX px
     -> compute.build_snapshot() -> greek_exposure table (append-only)
     -> Quality: STALE if age > CHANAKYA_GREEKS_STALE_SEC (90) ;
                 PARTIAL if band coverage < 80% ; else VALID
```

`greeks_engine` GREEKS tuple = `(delta, gamma, theta, vega)` — **rho never**
(AngelOne does not return it).

---

## 3. The two segments

| dimension | **NSE** (index/equity F&O) | **MCX** (commodity options) |
|---|---|---|
| example symbols | NIFTY, BANKNIFTY, FINNIFTY | NATURALGAS, CRUDEOIL, CRUDEOILM, GOLD, SILVER |
| broker optionGreek endpoint | **supported** — returns Δ Γ Θ V IV rows | **not supported** — `AB9019 NO_DATA`, always |
| primary Greek source | `BROKER` (AngelOne optionGreek) | `SYNTHETIC` (BS from `sr_engine._gex_profile`) |
| fallback Greek source | `SYNTHETIC` (only if broker `NO_DATA`/`STALE` and a chain is present) | none (synthetic *is* the source) |
| Greeks available | Δ, Γ, Θ, V, IV | Γ only (BS gamma); Δ/Θ/V **absent**, not zero |
| exposure metrics feasible | full `greek_exposure` row | gamma-exposure subset only (`ce/pe/net/diff_gamma_exp`, `gamma_conc_*`, `pcr_oi`); other `*_exp` = `NULL` |
| typical IV / sigma | 0.05 – 0.35 | 0.30 – 1.60 |
| IV sanity band (`iv_expected_band`) | 0.03 – 0.90 | 0.20 – 2.50 |
| freshness threshold (`stale_sec`) | 90 s | 150 s |
| min band coverage for `VALID` | 80 % | 60 % (thinner, wider-spaced MCX ladders) |
| underlying price source | NFO front-month FUTURE, then INDEX | MCX front-month FUTURE |

---

## 4. `GREEKS_PROFILES` — the policy object

A pure-data module, no logic, no I/O. Proposed `app/greeks_engine/profiles.py`:

```python
# DESIGN SKETCH — not code to merge as-is.
from dataclasses import dataclass

@dataclass(frozen=True)
class GreeksProfile:
    segment: str                 # "NSE" | "MCX"
    greek_source: str            # "BROKER" | "SYNTHETIC"
    broker_endpoint_supported: bool
    synthetic_fallback: bool     # allow BS synthetic when broker unavailable
    greeks_available: tuple      # subset of ("delta","gamma","theta","vega")
    iv_expected_band: tuple      # (lo, hi) — FLAG outside, never clamp
    stale_sec: float
    min_coverage_pct: float
    price_kinds: tuple           # order to try in quote_snapshots

NSE = GreeksProfile(
    segment="NSE", greek_source="BROKER", broker_endpoint_supported=True,
    synthetic_fallback=True,
    greeks_available=("delta","gamma","theta","vega"),
    iv_expected_band=(0.03, 0.90), stale_sec=90.0, min_coverage_pct=80.0,
    price_kinds=("FUTURE","INDEX"),
)
MCX = GreeksProfile(
    segment="MCX", greek_source="SYNTHETIC", broker_endpoint_supported=False,
    synthetic_fallback=False,
    greeks_available=("gamma",),
    iv_expected_band=(0.20, 2.50), stale_sec=150.0, min_coverage_pct=60.0,
    price_kinds=("FUTURE",),
)

# symbol -> segment. Reuse worker._EXCH_OF as the single source of truth;
# this map is only the fallback / explicit override.
SEGMENT_OF = {
    "NIFTY": "NSE", "BANKNIFTY": "NSE", "FINNIFTY": "NSE",
    "NATURALGAS": "MCX", "CRUDEOIL": "MCX", "CRUDEOILM": "MCX",
    "GOLD": "MCX", "SILVER": "MCX",
}
PROFILES = {"NSE": NSE, "MCX": MCX}

def profile_for(symbol: str) -> GreeksProfile:
    seg = SEGMENT_OF.get(symbol.upper()) or _EXCH_OF.get(symbol.upper(), "NSE")
    return PROFILES.get(seg, NSE)          # unknown -> NSE (broker-first) is safe:
                                           # a NO_DATA just logs, never fabricates
```

All numeric bands overridable by env (see §8) so tuning needs no redeploy of the
map. Defaults above are the starting point, not final — FLAG A / evidence in
`GEX_SR_SPEC.md §11` feeds the MCX numbers.

---

## 5. Touch-points (exactly four)

### T1 — skip the dead broker call for MCX
**File:** `app/histcap/worker.py:285 _capture_greeks()`
**Now:** unconditionally `sdk.get_option_greeks(sym, expiry)` for every symbol.
**Change:** at the top,
```
prof = profile_for(sym)
if not prof.broker_endpoint_supported:
    counts["greeks_skipped_mcx"] = counts.get("greeks_skipped_mcx", 0) + 1
    return                      # no POST, no raw row, no NO_DATA error spam
```
**Effect:** one fewer authenticated request per MCX symbol per cycle; the run-log
`errors[]` stops filling with `NO_DATA`. Raw-response history for MCX optionGreek
was never useful (always `AB9019`). NSE path unchanged.
**Safety:** pure removal of a call that always fails. No data lost.

### T2 — make the per-cycle engine call segment-aware
**File:** `app/histcap/worker.py:182-187`
**Now:** `if counts["greeks"] > 0 and "NIFTY" in symbols: greeks_engine().run_once("NIFTY", …)`
**Change:** iterate configured symbols; for each, resolve profile and call
`run_once(sym, profile=prof, mode="CYCLE")`. NSE symbols run only when that
symbol wrote broker Greeks this cycle; MCX symbols run when a chain snapshot with
OI exists this cycle (synthetic path needs OI + chain, not `option_greeks`).
**Effect:** BANKNIFTY (if captured) also gets an exposure row; MCX gets a
gamma-only exposure row derived from synthetic gamma.
**Safety:** still read-only, still append-only, still `try/except` so a failure
never touches capture.

### T3 — `run_once` sources Greeks per profile
**File:** `app/greeks_engine/engine.py:93 run_once()`
**Now:** always reads `option_greeks` (`broker_status='OK'`).
**Change:** add `profile: GreeksProfile | None = None` (default `profile_for(underlying)`).
- `profile.greek_source == "BROKER"` → current path unchanged. If it yields
  `NO_DATA`/`STALE` **and** `profile.synthetic_fallback` and a chain snapshot is
  available → build a synthetic row instead, tagged `source="SYNTHETIC_BS_GAMMA"`.
- `profile.greek_source == "SYNTHETIC"` → skip `option_greeks` entirely; pull the
  per-strike gamma from `sr_engine._gex_profile(...)`'s `per_strike:[{strike,
  ce_oi, pe_oi, gamma, shape}]` for that underlying's latest in-hours snapshot
  (or recompute from the captured chain + OI). Feed those into
  `compute.build_snapshot()` with `delta=theta=vega=None` for every leg — the
  existing "missing Greek → pair dropped, never 0" rule already produces
  `ce/pe/net/diff_gamma_exp` and leaves the other `*_exp` `NULL`. No new math.
**Effect:** MCX `greek_exposure` rows exist, honest about being gamma-only and
synthetic.
**Safety:** `build_snapshot()` already fabricates nothing; synthetic gamma is
clearly labelled and confined to `greeks_engine` + `sr_engine` (never the capture
layer — same rule as GEX v1a).

### T4 — quality / band checks read the profile
**File:** `app/greeks_engine/compute.py:build_snapshot()` + `engine.STALE_SEC`
**Now:** `stale_sec_threshold` passed in (module const 90); coverage `< 80` →
`PARTIAL`; no IV band check.
**Change:** `build_snapshot()` takes `profile` (or the three scalars
`stale_sec`, `min_coverage_pct`, `iv_expected_band`). Use `profile.stale_sec` for
`STALE`, `profile.min_coverage_pct` for `PARTIAL`, and add a **non-blocking**
`iv_oob` flag when `oi_weighted_iv` falls outside `profile.iv_expected_band`
(recorded in the row's flags, **never clamped**, does not change `quality`).
**Effect:** MCX sigma ≈ 1.0 is `VALID`, not mislabelled; a genuinely broken IV
still shows up as `iv_oob`.
**Safety:** thresholds only; no clamping; `INVALID` (non-finite aggregate) rule
unchanged.

---

## 6. Data-model deltas

`greek_exposure` (in `market_history.db`) — **add two nullable columns**, no
change to existing ones, no migration of old rows:

| column | values | meaning |
|---|---|---|
| `segment` | `NSE` \| `MCX` | which profile produced this row |
| `greek_source` | `BROKER` \| `SYNTHETIC_BS_GAMMA` | broker Δ/Γ/Θ/V vs BS-gamma-only |

`source` (already `NOT NULL`, currently always
`DERIVED_FROM_ANGELONE_OPTION_GREEK`) stays as the top-level provenance string;
`greek_source` is the finer split. A consumer that wants only broker-grade rows
filters `greek_source='BROKER'`.

`option_greeks` (capture table) — **no change**. MCX simply stops writing to it
(it never did, beyond `NO_DATA`).

`greek_engine_runs` — `notes` already free-text; include `segment` + `profile`
there. Optionally add `segment` column for cheap filtering (nullable).

---

## 7. Config keys (all optional, env-driven)

| key | default | effect |
|---|---|---|
| `CHANAKYA_GREEKS_STALE_SEC` | 90 | **NSE** freshness (unchanged name/default) |
| `CHANAKYA_GREEKS_STALE_SEC_MCX` | 150 | MCX freshness |
| `CHANAKYA_GREEKS_MINCOV_NSE` | 80 | NSE `PARTIAL` cutoff |
| `CHANAKYA_GREEKS_MINCOV_MCX` | 60 | MCX `PARTIAL` cutoff |
| `CHANAKYA_GREEKS_IVBAND_NSE` | `0.03,0.90` | NSE `iv_oob` flag bounds |
| `CHANAKYA_GREEKS_IVBAND_MCX` | `0.20,2.50` | MCX `iv_oob` flag bounds |
| `CHANAKYA_GREEKS_MCX_SYNTHETIC` | `1` | master switch — `0` disables the MCX synthetic exposure path entirely (T2/T3 no-op for MCX), leaving only T1's dead-call skip |

No new secret. Credentials still environment-only.

---

## 8. What explicitly does NOT change

- `live_trading` stays `false`; `paper_mode` stays `true`. No order path, no
  broker order method, no SDK order import.
- No change to `_candidates` / `_strength` / signal thresholds / probability cap /
  trading-strategy logic. GEX v1a stays diagnostic-only.
- The capture layer (`app/histcap`) still writes **only** broker-sourced Greeks to
  `option_greeks`. Synthetic gamma lives solely in `sr_engine` +
  `greeks_engine` derived tables — same boundary as today.
- `greeks_engine` remains **analysis/confirmation only**. It emits no BUY CE / BUY
  PE / NO TRADE, no confidence, no entry/SL/target. This spec does not give it a
  signal role.
- No fabrication: a missing Greek is `None` and drops the pair; MCX Δ/Θ/V are
  reported absent, not zero.
- AngelOne optionGreek remains the **only** broker Greek source; nothing here adds
  a second broker or a scraped source.

---

## 9. Test plan (for the eventual implementation)

`backend/tests/test_greeks_profiles.py` (new) + additions to
`test_greeks_engine.py`:

1. `profile_for("NIFTY")` → NSE; `profile_for("NATURALGAS")` → MCX;
   `profile_for("UNKNOWNSYM")` → NSE (safe default).
2. T1: MCX symbol → `_capture_greeks` returns early, `sdk.get_option_greeks`
   **not called** (mock asserts 0 calls), NSE symbol still calls it.
3. T3 BROKER path: seeded `option_greeks` OK rows → identical `greek_exposure`
   row as today (regression lock).
4. T3 SYNTHETIC path: seeded chain + OI, no `option_greeks` rows → row with
   `greek_source='SYNTHETIC_BS_GAMMA'`, `segment='MCX'`, `ce/pe/net/diff_gamma_exp`
   populated, `*_delta/theta/vega_exp` all `NULL`, `pcr_oi` populated.
5. T4: MCX row with `oi_weighted_iv=1.05` → `quality=VALID`, `iv_oob` **not** set
   (inside 0.20–2.50); with `iv_weighted_iv=3.0` → `iv_oob` set, `quality` still
   `VALID` (flag is non-blocking).
6. T4: MCX Greek aged 120 s → `VALID` (threshold 150); NSE Greek aged 120 s →
   `STALE` (threshold 90).
7. Coverage 65 % → `PARTIAL` for NSE, `VALID` for MCX.
8. Look-ahead safety unchanged (`history(as_of=…)` still filters `as_of_ts`).
9. Idempotency unchanged (2nd `run_once` in a cycle writes 0).
10. Full backend suite stays green; `compileall` clean.

---

## 10. Rollout / evidence gates

1. **Doc approved** (this file).
2. Implement T1 only (dead-call skip) — zero behavioural risk, ship with the next
   authorised restart. Watch `greeks_skipped_mcx` counter + confirm NSE Greek
   capture rate unchanged for 1 session.
3. Implement T2–T4 behind `CHANAKYA_GREEKS_MCX_SYNTHETIC=0` (off). Land code +
   tests. No runtime change yet.
4. Flip `=1` in a market-closed window. Collect ≥3 MCX sessions of
   `greek_exposure` rows. Verify: `segment`/`greek_source` correct, MCX
   `quality` mostly `VALID`, `iv_oob` rare, gamma-exposure sign tracks
   `gex_regime_sign`.
5. Only then consider whether MCX gamma-exposure adds anything the existing GEX
   columns don't (this is the A2 question — measure, don't assume).

No step here enables candidates/strength for MCX or changes any signal. That
remains the separate, still-unapproved GEX roadmap (A2 → A3 → A4).

---

## 11. Open questions for the approver

- **Q1** MCX synthetic exposure — build it now (T2–T4), or stop at T1 (skip the
  dead call) and leave MCX Greek-exposure empty until there's a concrete use for
  it? T1 alone is a clean, safe win; T2–T4 is real new surface area for a metric
  nobody consumes yet.
- **Q2** Include BANKNIFTF in NSE capture now, or keep NSE = NIFTF-only until
  BANKNIFTF is actually needed? (Its Greek population is currently ~4 %.)
- **Q3** Add `segment`/`greek_source` columns to `greek_exposure` (needs a tiny
  `ALTER TABLE` on the existing DB), or encode both inside the existing `source`
  string to avoid touching schema?
- **Q4** MCX `stale_sec=150` and `iv_expected_band=(0.20, 2.50)` — accept these
  starting values, or set from a query over the last N MCX sessions first?
