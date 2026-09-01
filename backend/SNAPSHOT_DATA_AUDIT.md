# Market-Snapshot Data Audit — `live_market_snapshots`

**Date:** 2026-09-01 · **Scope:** the existing `live_market_snapshots` store only (the canonical
per-cycle record the autonomous scalper writes). **Read-only.** No trading logic, broker, or
schema changes were made. NATURALGAS was used only as an already-working reference point — no
NG-specific analysis, logic, indicator, correlation or regression was added.

Sample audited: **~7,010 rows**, sessions **2026-08-31** (5,046) and **2026-09-01** (1,964).
Per symbol: NIFTY 2,760 · NATURALGAS 2,123 · CRUDEOIL 2,123 · BANKNIFTY 4 (stale, see DQ-3).

---

## 1. Snapshot completeness

### Always populated (100%, every row, every symbol)
`id, ts, session_date, symbol, source, provenance, index_ltp, atm, regime, signal_type,
direction, decision, reason, chain_json` (chain_json is the literal `[]` when the market is
closed — present but empty).

### Populated only on a full open-market evaluation (≥20×5m bars, market OPEN)
`atr, support, resistance, support_strength, resistance_strength, mtf_alignment`, plus `vwap`
for NATURALGAS/CRUDEOIL. Coverage: **NIFTY 13.4%**, **NATURALGAS 46.9%**, **CRUDEOIL 46.9%**.
The gap is almost entirely `regime = MARKET_CLOSED` rows (see §2, DQ-1).

### Populated only when the decision reached the scoring stage (advisory / WATCH / BUY / EV-gate)
`signal_score` (NIFTY 8.5% / NG 30.4% / CRUDE 28.8%), `probability` (same), `confidence`
(subset of `probability`: NIFTY 7.2% / NG 22.4% / CRUDE 21.5%).

### Populated only when a contract was actually selected (EV gate reached)
`ev`, `rr` — NIFTY 6.4% / NG 14.4% / CRUDE 15.7%. On the rows that matter (every `BUY_*`) they
are **100% present** — see §6.

### Never populated — 100% NULL in every row
`pcr`, `max_pain`. The autoscalp writer (`runner._persist_snapshot`) does not pass these keys,
so `insert_live_snapshot` stores `NULL` unconditionally. Same in the sibling `scalp_signals`
table (`pcr`, `max_pain`, `momentum`, `vwap` all NULL across its 12 rows).

### Recently added — sparse by age, not by fault
`vwap_status` — only on rows written since the 2026-09-01 fix (~562 rows). Where present it is
**fully consistent**: NIFTY → `invalid_volume` (113), NATURALGAS/CRUDEOIL → `available` (228 each).
0 contradictions (no `available` with NULL vwap; no `invalid_volume`/`insufficient_data` with a
vwap value).

### Stale / duplicate
- **Duplicate timestamps:** 0 (exact-duplicate `ts` per symbol = 0).
- **Near-identical consecutive rows:** NIFTY 2,112 / NG 1,381 / CRUDE 1,264 — virtually all are
  back-to-back `MARKET_CLOSED` heartbeat rows with identical `index_ltp`/`regime`/`decision`
  (DQ-1).
- **Stale symbol:** BANKNIFTY — 4 rows, all `MARKET_CLOSED`, 2026-08-31 13:13–13:15 UTC, a
  leftover from a one-time config drift. Never traded, never re-appeared (DQ-3).

---

## 2. Data quality

### Timestamps & sequencing — clean
- **0 negative gaps** (monotonic per symbol).
- Cadence: p50 ≈ 30.3 s, p95 ≈ 32 s (matches `decide_every_sec`).
- Largest gaps: NIFTY up to ~7,935 s, MCX up to ~4,344 s — overnight / session-boundary pauses,
  not data loss.

### OHLC consistency
`live_market_snapshots` stores **no OHLC bar series** — only `index_ltp` (last price) plus the
`chain_json` blob. This is by design (schema comment: *"the minimum needed to replay/audit/
recalibrate a live signal later"*). OHLC lives in the in-memory `CandleAggregator` and is not
persisted here, so there is nothing to cross-check at the snapshot level. **DATA_INSUFFICIENT**
for a stored-OHLC consistency check (nothing stored).

### Indicator values — no impossible or anomalous values (2,758 open-market rows)
| invariant | violations |
|---|---|
| `support < resistance` | **0** |
| `support_strength`, `resistance_strength` ∈ [0,100] (observed 6.0 – 83.8) | **0** |
| `probability` ∈ [0,1] (observed 0.352 – 0.640) | **0** |
| `atr > 0` (observed 0.114 – 33.79) | **0** |
| `atr` < 5% of `index_ltp` | **0** |
| `|index_ltp − support|` < 10% | **0** |
| `mtf_alignment` ∈ [−100,100] (observed −73 – 73) | **0** |

Note: `probability` sits in a narrow **0.35–0.64** band across all symbols — the calibrator has
limited discriminating power at this sample size. Not a data defect; a modelling observation.

### DATA_UNAVAILABLE vs genuine zero
- No `reason` string contains `DATA_UNAVAILABLE`, `unavailable`, or `insufficient` — the engine's
  early "not enough bars" return happens *before* `_persist_snapshot`, so those cycles produce
  **no row at all** (correct: absence, not a fake zero).
- `feed_age_sec`: 99.9% present; **9 NULL** (unknown, stored as NULL — correct) and **189 exact
  `0.0`** (a tick had just landed — genuine, not a coerced unknown, since unknowns are NULL).
- `mtf_alignment` has **219 genuine `0.0`** values (no directional alignment) — a real reading,
  not missing data.

---

## 3. What the snapshots can reliably tell the AI about market state

| dimension | reliably available? | from which field(s) | notes |
|---|---|---|---|
| **trend** | ✅ on open rows | `regime` (100% populated as a label; `TRENDING_UP/DOWN`, `RANGE`, `UNSTABLE`, + `BREAKOUT/REVERSAL/HIGH_VOLATILITY` on MCX) | categorical only; no trend *slope* stored |
| **momentum** | ❌ **not stored** | `signal_type` + `direction` give a setup label only | `momentum` (roc_pct) and `state_score` are computed every cycle and dropped — see PU-1 |
| **volatility** | ✅ on open rows | `atr` | sane range, positive, <5% of price |
| **support / resistance** | ✅ on open rows | `support`, `resistance`, `support_strength`, `resistance_strength` | invariants hold; OI is ~17% of the strength score (see `SR_ENGINE_AUDIT.md`) |
| **volume** | ⚠️ partial / indirect | none as a column | NIFTF (index) has none by construction; MCX volume is real but only surfaces *indirectly* as `vwap` being non-NULL |
| **OI** | ⚠️ blob only | inside `chain_json`; `pcr`/`max_pain` columns are 100% NULL | no extracted OI scalar in the row |
| **price action** | ⚠️ last-price only | `index_ltp`, `atm` | no OHLC / candle persisted here by design |
| **VWAP status** | ✅ (post-fix) | `vwap`, `vwap_status`, (`vwap_reason` not a column) | NIFTY correctly `invalid_volume`; MCX `available` |
| **regime** | ✅ | `regime` | 100% populated, clean categorical |

---

## 4. Signal usefulness — which fields actually drive BUY / NO_TRADE

Traced through `scalp_strategy.decide_from_context()`:

**Directly gate the decision:**
`regime` (blocked-regime filter), `signal_type` (blocked-type filter + BULLISH/BEARISH → CE/PE),
`mtf_alignment` (MTF opposing-structure gate), `support`/`resistance` + strengths (feed state
classification & anchor via `compute_sr`), `signal_score` → `probability` → `confidence`
(min-confidence / WATCH downgrade), `ev` / `rr` (EV gate), `atr` (expected-move & position
sizing). `option_quality` also gates but is **not persisted**.

**Marginal:** `vwap` — only a **0.03 weight** in the S/R strength blend, and **zero effect on
NIFTY** (never available). `vwap_status` is diagnostic only; it does not gate.

**Not consumed by the autoscalp decision path at all:**
`pcr`, `max_pain` (NULL and no reader), `source` (constant `"LIVE"`), `provenance` (near-constant).

**Redundant (derivable):** `direction` (fully determined by `signal_type` via the BULLISH set),
`atm` (= `round(index_ltp / strike_step) * strike_step`).

---

## 5. Historical snapshot audit — recurring pre-signal patterns

**DATA_INSUFFICIENT.** Only **12 closed AUTOSCALP trades exist** (5 WIN / 4 LOSS / 3 FLAT;
exits: 8 TIME, 2 TRAIL, 2 STOP; NIFTY 4, NATURALGAS 8, CRUDEOIL 0). Every closed trade matched
a `BUY_*` snapshot within 120 s, but n = 12 cannot support any claim that a pre-signal snapshot
pattern improves signal quality.

Directional-only, **not significant** (reported for completeness, not for action):
- `ev` above sample-median → 3/5 win vs 2/7 below.
- `confidence = MEDIUM` → 2/3 vs `LOW` → 3/9.
- `probability` / `signal_score` / `rr` split the 12 outcomes ~evenly (no separation).

A meaningful pass needs **≥ ~30–50 closed AUTOSCALP trades with `risk_ref`** — realistically
weeks of PAPER. Re-run when the sample is there. **No fabricated / backfilled values were used
or are recommended.**

---

## 6. AI decision audit — is the strategy deciding from valid data?

**Yes, on the rows that execute.** Across **447 open-market `BUY_*` snapshots**:
- rows missing `atr` / `support` / `resistance`: **0**
- rows missing `signal_score` / `probability` / `ev`: **0**
- rows missing `confidence`: **0**

Every actual trade decision carried a complete analytic payload. Missing-data cycles resolve to
`NO_TRADE` (safe direction): `compute_sr` status ≠ OK → `NO_TRADE "S/R unavailable"`; <20 bars →
no row written at all.

**Two places where missing data is treated as a value rather than excluded** (both currently
land on the *conservative* side, both flagged, neither changed here):

- **FIX-1 — VWAP-missing scored as worst-case zero.** In `sr_engine._strength()`,
  `vwap_prox = 0.0` when `vwap is None`, but its `0.03` weight stays in the sum (the base is not
  renormalised). Effect: **every NIFTY S/R zone strength is depressed by up to 3 points** purely
  because an index has no volume. It is symmetric (support and resistance both) and
  ranking-preserving, but it *is* missing data scored as zero. The `mode != "index"` branch
  already demonstrates the correct idiom (redistribute an inapplicable factor's weight). See FIX
  section — not applied here because it shifts NIFTY strength values and therefore needs the same
  PAPER-evidence bar as any strength-formula change.

- **OBS — thin MTF history reads as "neutral / aligned".** `mtf_alignment ≈ 0` on short history
  passes the `abs(alignment) ≤ 12` "aligned" test, so a sparse MTF read does not block. Many
  other gates still apply. Document; do not change (it is a trading-logic threshold).

`pcr` / `max_pain` being NULL is **harmless** — nothing in the decision path reads them, so the
NULLs cannot bias a signal.

---

## 7. Efficiency — fields unnecessary for future AI decisions

Verified against backend (`grep` over `app/`), frontend (`frontend/static/js/app.js`), reports
(`app/autoscalp/report.py`), and tests (`grep` over `tests/`):

- **No column is safe to physically drop right now.** Every column flows through `SELECT *` →
  `list_live_snapshots` → `/api/autoscalp/snapshots` → the dashboard, storage cost is trivial
  (~13 MB total DB), and several are audit-trail by design.
- `pcr` / `max_pain` are dead **as written today**, but the fix is to *populate* them (cheap,
  from `chain_json` already in hand, non-gating) rather than remove them — the name is shared
  with the active `scalp_signals` table and the turning-point / OI engines, and a `SELECT *`
  consumer would lose the key. See FIX-2.
- `source` is a constant but 4 bytes/row and reserved for a future multi-feed; keep.

---

## 8. Output — findings

### KEEP (working, used, reliable — no action)
| field | why |
|---|---|
| `ts`, `session_date` | indexed; drive `report.py` IST day rollups and all sequencing |
| `symbol`, `decision`, `regime`, `reason` | 100% populated; `report.py` GROUP BYs + `BLOCKED[…]` evidence |
| `index_ltp`, `atm` | price context used everywhere in the decision path |
| `atr`, `support`, `resistance`, `support_strength`, `resistance_strength` | core S/R inputs; invariants all hold; 0 BUY rows missing them |
| `signal_type`, `mtf_alignment`, `signal_score`, `probability`, `confidence`, `ev`, `rr` | the scoring/gating chain; 100% present on `BUY_*` rows |
| `vwap` (NG/CRUDE), `vwap_status` | post-fix, live-verified, consistent; drives the FE VWAP cell |
| `feed_age_sec` | 99.9% present; FE staleness banner + freshness audit |
| `provenance` | small; feed/owner attribution for replay |

### FIX
| id | field / code | issue | recommended change | risk |
|---|---|---|---|---|
| **FIX-1** | `sr_engine._strength()` `vwap_prox` | weight (0.03) not redistributed when `vwap is None` → NIFTY zone strengths depressed ≤3 pts | renormalise the weight base (or spill the 0.03 like the `mode != "index"` OI branch) when vwap is unavailable | **shifts NIFTY strength values → trading-logic-affecting**; gate behind PAPER evidence like any strength-formula change; do not ship blind |
| **FIX-2** | `runner._persist_snapshot` / `insert_scalp_signal` | `pcr`, `max_pain` columns 100% NULL though `chain_json` carries the data | compute PCR (`Σpe_oi / Σce_oi`) and max-pain from the chain already in scope and pass them; additive, **not** read by any gate | low — additive, non-gating; add a unit test asserting both are populated on an open-market row |
| **FIX-3** | `_persist_snapshot` | `momentum` / `state_score` computed in `ctx` every cycle, never persisted to `live_market_snapshots` (no column) | add `momentum REAL` (already a column in `scalp_signals`) + optional `state_score REAL` via the idempotent `_MIGRATIONS` pattern and write them | low — additive column + one writer line |

### UNUSED (present, not consumed by the autoscalp decision path)
- `pcr`, `max_pain` — NULL everywhere **and** no reader in `app/autoscalp/*`. → resolve via FIX-2
  (populate), not removal.
- `direction` — persisted but fully derivable from `signal_type`. Harmless; keep for
  human-readability of the raw table.
- `source` — constant `"LIVE"`. Keep (reserved).

### DATA QUALITY ISSUE
- **DQ-1 — MARKET_CLOSED heartbeat bloat.** ~61% of all rows (NIFTY 72%, MCX 53%) are
  `regime = MARKET_CLOSED` NO_TRADE rows written every ~30 s overnight/weekend, near-identical
  back-to-back (NIFTY 2,112 such consecutive pairs). They carry no analytic value (no
  atr/S-R/vwap, `chain_json = []`). They *do* serve as an "engine was alive" trail and feed
  `report.py`'s decision/regime counts. **Recommendation (not applied):** when
  `regime == MARKET_CLOSED`, either write at a slower cadence (e.g. once / 5 min) or skip a write
  when the previous row for that symbol is an identical MARKET_CLOSED — a small `_evaluate`
  change, needs care so the liveness/report semantics are preserved.
- **DQ-2 — 390 open-market NO_TRADE rows carry `regime` but NULL `atr`/`support`/`resistance`.**
  These are advisory / blocked-filter / "no clean state" / CE-PE-conflict returns. Not a
  correctness bug (all are NO_TRADE; no BUY row is affected) but a completeness inconsistency —
  the `ctx` block is not always merged into these early returns. **Recommendation:** make the
  advisory/`out_none` returns always carry the `ctx` analytic block so a NO_TRADE row is still a
  complete market read (consistent with the code comment that already claims this).
- **DQ-3 — BANKNIFTY 4 stale rows** (2026-08-31, config-drift residue). Cosmetic. **Do not
  auto-delete** (per instruction). A one-line `DELETE FROM live_market_snapshots WHERE
  symbol='BANKNIFTY'` is safe to run manually if desired.
- **DQ-4 — `probability` compressed to 0.35–0.64.** Modelling signal, not a storage bug; revisit
  calibration once the closed-trade sample supports it.

### POTENTIALLY USEFUL (cheap, already-computed, would materially help post-hoc AI analysis)
- **PU-1 — `momentum` (roc_pct) + `state_score`** — see FIX-3. Both are scalars produced every
  cycle in `ctx`; persisting them would give a stored momentum series (currently the single
  biggest market-state gap in §3).
- **PU-2 — `pcr` + `max_pain`** — see FIX-2. Turns the always-NULL columns into a real OI-context
  series without touching any gate.
- **PU-3 — a compact `sr_diag` / `component_scores` blob** — the per-factor S/R breakdown is
  computed each cycle and only reaches the live `/status` API. A capped JSON column (like
  `scalp_signals.component_scores`) would let a later audit see *why* a zone scored as it did.
  Lower priority (blob, not a scalar).

### DO NOT REMOVE
- `chain_json` — schema-documented purpose: replay / recalibration / signal audit. Capped at
  20 KB. Removing it makes historical re-scoring impossible.
- `ts`, `session_date` — indexed; every `report.py` query and all sequencing depend on them.
- `regime`, `decision`, `reason` — `report.py` GROUP BYs **and** the `BLOCKED[<gate>]` runtime
  evidence trail (task-6 CRUDEOIL block reason = `stale feed (>12 s)` is recovered entirely from
  `reason`).
- `vwap_status` — added and live-verified 2026-09-01; drives the FE "— n/a (no volume)" cell;
  removing it re-opens the bug it fixed.
- `feed_age_sec` — FE staleness banner + freshness audit.
- `signal_score`, `probability`, `confidence`, `ev`, `rr` — the calibration sample
  (`_maybe_recalibrate`) and every future signal-quality study read these.

---

## Safety / constraint check

| constraint | status |
|---|---|
| trading logic unchanged | ✅ no code changed — report only |
| broker / live-order path | ✅ untouched; `live_trading: false`, `paper_mode: true` |
| NATURALGAS logic / indicators / correlation | ✅ none added; NG used only as a reference column |
| fabricated / backfilled data | ✅ none — every recommendation uses real chain data or is a
  no-op |
| removals | ✅ none performed; every removal candidate downgraded to "populate" or "keep" |
| dependency verification before any removal recommendation | ✅ grep over `app/`, `frontend/`,
  `report.py`, `tests/` |
| tests after change | ✅ no change made; baseline re-confirmed — `test_sr_engine.py` +
  `test_autoscalp.py` + `test_p61_filters_ablation.py` = **53 passed** |

## If any FIX is approved
Recommended order, each independently shippable:
1. **FIX-2 + FIX-3** together (additive, non-gating): migration for `momentum`/`state_score`,
   compute `pcr`/`max_pain`/`momentum`/`state_score` in `_persist_snapshot`, add a test asserting
   an open-market row has all four non-NULL. Run `tests/test_autoscalp.py`.
2. **DQ-2**: always merge `ctx` into advisory / `out_none` returns; assert a NO_TRADE row still
   carries `atr`/`support`. Run `tests/test_scalp_strategy*.py` + `tests/test_p61_filters_ablation.py`.
3. **DQ-1**: MARKET_CLOSED write throttling — smallest change, most caution (report semantics).
4. **FIX-1**: `vwap_prox` weight redistribution — **only** with a PAPER A/B showing NIFTY signal
   quality is unaffected or improved; treat as a strategy change, not a bugfix.
