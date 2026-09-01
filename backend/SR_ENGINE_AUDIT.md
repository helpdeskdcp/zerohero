# S/R + OI-Wall Strength Engine — Audit

**Date:** 2026-09-01 · **Engine:** `app/engines/sr_engine.py` `compute_sr()` · **Commit:** `309db1e`
**Scope:** audit + safe fix only. No live trading, no broker execution, no risk/kill-switch/PAPER changes.

---

## A. Current formula

`compute_sr()` builds **S/R zones** (not bare points) from a weighted confluence of ~8 factors and
scores each zone 0–100. OI is **one** factor, not the model.

**Candidate levels** (`_candidates`): prev-day H/L/C, floor pivots, intraday + HTF swing pivots,
session HOD/LOD, round numbers, VWAP, and — index mode only — **the single strike with the largest
CE OI** (`oi_wall_ce`), **the single strike with the largest PE OI** (`oi_wall_pe`), and the
largest positive CE/PE OI-change strikes (`oi_write_*`).
OI-wall candidate base weight = `0.5 + 0.8 · (wall_OI / total_side_OI)`.

**Clustering** (`_cluster`): candidates within `max(0.25·ATR, price·1e-4)` merge into one zone;
zone level = OI-weighted mean of members.

**Strength** (`_strength`) = `100 · Σ cₖ·wₖ` over:

| component | weight (index) | what it measures |
|---|---|---|
| confluence | 0.24 | distinct source families in the zone / 4 |
| touch_quality | 0.20 | rejections / touches |
| rejection_count | 0.16 | rejections / 3 |
| recency | 0.10 | 1 − age(last touch)/n |
| htf_agree | 0.10 | 1 if an HTF or prev-day family is present |
| **oi_backing** | **0.12** | near-strike (CE+PE) OI ÷ avg per-strike OI ÷ 3, capped 1 |
| **oi_change_backing** | **0.05** | near-strike \|ΔOI\| ÷ avg ÷ 2, capped 1 |
| vwap_prox | 0.03 | 1 − \|level−VWAP\| / (1.5·ATR) |

→ **OI contributes ≈ 17 %** of the score; price-structure ≈ 83 %.
`_best()` then selects, per side, the highest-strength zone within 4 ATR of spot (ties broken by
nearness); if none within 4 ATR, all zones are eligible.

`mode="option"` zeros the OI weights and redistributes them to the structural factors.

## B. Raw OI inputs (the audited NATURALGAS snapshot, spot ≈ 278.70, 1 L = 100 000)

| strike | Call OI | Put OI |
|---:|---:|---:|
| 255 | 1.98 L | 44.99 L |
| 260 | 21.65 L | 82.21 L |
| 265 | 12.01 L | 53.24 L |
| **270** | 49.70 L | **96.85 L** ← max PE |
| 275 | 38.24 L | 68.19 L |
| **280** | **94.09 L** ← max CE | 70.22 L |
| 285 | 36.68 L | 19.41 L |
| 290 | 57.94 L | 15.30 L |
| 295 | 20.30 L | 2.34 L |

`Σ CE OI = 332.59 L`, `Σ PE OI = 452.75 L`, avg per-strike total OI = `87.26 L`.

## C. Independent recalculation

**OI-wall candidates:**
- `oi_wall_ce` → strike **280**, weight `0.5 + 0.8·(94.09/332.59) = 0.726` ✓ (matches engine)
- `oi_wall_pe` → strike **270**, weight `0.5 + 0.8·(96.85/452.75) = 0.671` ✓
- **Only these two strikes get an OI candidate.** 275 PE (68 L), 265 PE (53 L), 290 CE (58 L) get
  nothing from the OI factor — they only participate if a swing/pivot lands on them.

**`oi_backing` component** (÷ avg ÷ 3, cap 1):

| zone near | near-strike total OI | ratio to avg | oi_backing | pts to score (·0.12·100) |
|---:|---:|---:|---:|---:|
| 280 | 164.31 L | 1.88× | 0.628 | **7.5** |
| 275 | 106.43 L | 1.22× | 0.407 | 4.9 |
| 270 | 146.55 L | 1.68× | 0.560 | **6.7** |
| 290 | 73.24 L | 0.84× | 0.280 | 3.4 |

→ **The 280 band's OI backing (0.628) is *higher* than the 270 band's (0.560)** — the OI factor
correctly favours 280. Any strength gap between support and resistance is **not** coming from OI.

**Full `compute_sr` run** (real chain + a plausible ~278.7 tape): the 280 CE wall and 270 PE wall
each form an **isolated 1-family (`{"oi"}`) zone** that does not merge with any swing/pivot
(nearest swing was > 0.25 ATR away). Such a zone scores only `confluence 0.25·0.24 + oi_backing
0.628·0.12` ≈ **13 / 100**. `_best()` skips it and selects a nearby **swing/session zone** as the
support / resistance instead. That selected zone borrows the 280 band's `oi_backing` (0.628) via
the near-strike lookup.

## D. Calculated strengths vs the dashboard (SUP STR 65 / RES STR 34)

**Mathematically consistent with the model.** RES < SUP despite the biggest wall sitting at
resistance is expected: OI is 17 % of the score, and at that snapshot price was pushing *up into*
280 (few prior rejections there → low `touch_quality` / `rejection_count` / `recency` for the
resistance zone) while the support band near 270–273 had swing-low + pivot + prev-day confluence
and recent rejections. The engine is doing what its design says.

## E. Bug found

**Zero / missing OI created a phantom wall.** When every `ce.oi` (or `pe.oi`) in the chain was
`None` / `0`, `max(chain, key=lambda r: oi or 0)` returned `chain[0]`, and the candidate
`(chain[0].strike, "oi_wall_ce", 0.5 + 0.8·0) = 0.5` was still appended — a fake OI wall at the
first strike, adding a fake `oi` family to `confluence`. `oi_write_*` was already guarded; the
`oi_wall_*` path was not.

Everything else checked out:
- No division by zero (`… or 1.0` / `… or 1e-9` guards throughout).
- CE and PE normalised consistently (each `oi/total_side_OI`; `oi_backing` uses combined CE+PE).
- Deterministic (`test_deterministic`).
- **Expiry:** in the autoscalp path the chain is single-expiry *by construction*
  (`get_option_chain` filters `r.expiry == selected`; verified live = `23SEP2026`, the front NG
  monthly). `compute_sr` itself does not re-check, so a caller passing a mixed-expiry chain would
  sum OI across expiries — defensive gap only, not reachable in production.
- **Staleness:** `compute_sr` has no OI-timestamp check; it trusts the chain it is handed. The
  autoscalp path re-fetches the chain every decide cycle, so "stale" would mean the broker
  returned stale OI, which the app cannot detect. Documented limitation.
- **ΔOI:** `oi_chg` is `None` on the live MCX chain (the MCX quote path does not carry OI-change),
  so `oi_change_backing` and `oi_write_*` are inert for NG/CRUDE — degrade cleanly to 0, no error.

## F. Fix applied

1. **Bug E:** only emit `oi_wall_ce` / `oi_wall_pe` when the winning strike's OI is actually `> 0`.
   Verified **byte-identical** strength scores (support 66.4 / resistance 78.5 and all 19 zone
   strengths) before vs after on an OI-present chain → **no strategy / risk / decision change** for
   NIFTY / NG / CRUDE (all carry OI).

2. **Diagnostic (read-only, does NOT feed the model):**
   - `compute_sr(…, symbol=None)` — optional, back-compatible kwarg.
   - `return["sr_diag"]`:
     - `oi_walls`: `symbol`, `expiry`, `spot`, and per side the **top-3** OI strikes with
       `strike`, `oi`, `oi_chg`, `dist_pct`, `raw_wall_score` (share of side OI), and an
       **illustrative** `dist_weighted_score` (linear decay to 0 by 5 % of spot) — clearly marked
       as not used by the score.
     - `support` / `resistance`: the selected level's `symbol`, `expiry`, `spot`, `level`, `zone`,
       `strength`, `dist_pct`, `dist_atr`, `touches`, `rejections`, `nearest_strike`, `call_oi`,
       `put_oi`, `oi_change`, full `components`, and a `reason` string.
   - Surfaced through `decide_from_context()` (NO_TRADE ctx + BUY return) and the runner passes
     `symbol` into `strat_cfg`.

**NOT changed** (would be tuning on a single snapshot — the brief forbids it; any predictive
change must be validated on historical / live PAPER evidence):
- top-K OI walls per side as candidates (only the single max is used today);
- a distance-decay term on the OI-wall candidate weight;
- a floor strength for an isolated OI-wall zone so it is not ignored;
- per-symbol `round_step` (index mode hard-codes 50, so round-number candidates for NG land at
  250 / 300 and are inert — a lost minor confluence source, not harmful).

These are captured as an evidence-gated backlog in §K of `PRODUCTION_READINESS.md`.

## G. Tests added (`tests/test_sr_engine.py`, 8 → 14)

| test | asserts |
|---|---|
| `test_ng_snapshot_oi_walls_recognised_as_candidates` | 280 CE & 270 PE become `oi_wall_*` zones |
| `test_ng_snapshot_oi_is_a_minor_factor_not_dominant` | selected S/R are price-structure zones, not lone OI walls; a `{"oi"}`-only zone scores < 45 |
| `test_ng_snapshot_diagnostic_shape` | `sr_diag` has all required fields; 290 CE wall present but distance-penalised vs 280 |
| `test_zero_or_missing_oi_creates_no_phantom_wall` | all-missing OI → no `oi_*` candidate; PE-only chain → only `oi_wall_pe` |
| `test_shared_engine_nifty_and_crude_still_ok` | NIFTY + CRUDE chains still score 0–100 with the new kwarg/diag |
| `test_compute_sr_symbol_kwarg_is_optional_and_backwards_compatible` | old call == new call; `sr_diag` present |

Full suite: **283 passed** (was 277). `compileall` clean.

## H. Before vs after

| | before | after |
|---|---|---|
| strength scores, OI-present chain (NIFTY/NG/CRUDE) | — | **identical** (proven) |
| chain with all-missing OI | phantom `oi_wall` at `chain[0]`, w=0.5 | no OI candidate |
| `compute_sr` return | no `sr_diag` | `+ sr_diag` (read-only) |
| `compute_sr` signature | `(bars_by_tf, *, chain, prev_day, mode, config)` | `+ symbol=None` (optional) |
| live NG `sr_diag.oi_walls` | n/a | top CE=280 (raw 0.34), top PE=270 (raw 0.36, dist-weighted 0.13 for the 3.2 %-away wall) |

## I. Safety verification

| check | state |
|---|---|
| `/api/health` | `live_trading: false`, `paper_mode: true` |
| `/api/autoscalp/status` | `live_trading: false`, `paper_mode: true`, `last_error: null` |
| `/api/autoscalp/selfcheck` `live_trading_disabled` | `true` |
| kill switch | present, `active: false`, policy `MONITOR` — untouched |
| broker execution code | not touched (engine change is `sr_engine.py` / `scalp_strategy.py` / `runner.py` config line only) |
| risk limits / safeguards | not touched |
| RAM | service RSS 215 MB, system 36 % — no OOM, no unbounded growth (`sr_diag` dicts are per-cycle, GC'd) |
| tests | 283 passed |
| evidence collectors | uninterrupted across the restart (pids 3480137 / 3490027 / 3508185 alive) |

**Operational note:** the post-deploy restart happened to coincide with the broker's **historical
candle REST endpoint returning `DATA_UNAVAILABLE` ("network error contacting broker")**. The WS tick
feed is healthy (`feed_age ≈ 0.1 s`); only `_seed_aggs()`'s broker backfill failed, so the
aggregators are rebuilding 5-m bars from live ticks and `selfcheck.ok` is transiently `false`
(`all_aggs_seeded: false`) until they reach 20 bars. This is a pre-existing restart/broker-outage
interaction, **not** caused by the S/R change (which is score-neutral) — it self-heals as bars build
or on the next restart once the broker endpoint recovers.

## Correctness vs data quality vs predictive validity — kept separate

- **Mathematical correctness:** the formula computes exactly what §A states; one real bug (phantom
  wall) fixed; no div-by-zero; deterministic.
- **Data quality:** `oi_chg` absent on MCX; no OI-staleness signal; `oi_backing`'s `÷ avg` yardstick
  is chain-window-width sensitive. All documented; none produce errors.
- **Predictive validity:** **not established.** Whether "17 % OI weight + isolated walls ignored" is
  the *right* model for NG is an open question that needs historical / live PAPER outcome data —
  explicitly out of scope for this snapshot audit.
