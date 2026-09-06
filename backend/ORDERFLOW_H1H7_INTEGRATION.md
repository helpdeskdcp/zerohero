# H1 / H7 Structural State Engine — SHADOW / OBSERVATION integration

**Status: implemented, READ-ONLY SHADOW / OBSERVATION MODE.**

This wires the Stage-3 → Stage-8 order-flow *research* into the application as a
deterministic, explainable **market-structure classifier**. It only *describes*
the state of a completed 5m bar and which research confidence that carries. It
is **not a trading-signal generator**:

- no order, no broker call, no execution path
- no notification
- no BUY / SELL / CALL / PUT / ENTRY / ORDER semantics, no auto-execution
- no option-premium inference (the underlying structural state is never turned
  into an option P&L or recommendation)
- no weighted / AI / institutional / smart-money / confidence score
- no invented data — anything unavailable is marked `UNOBSERVABLE`
- `live_trading` stays `false`, `paper_mode` stays `true`; existing production
  behaviour is unchanged

Stage-7 and Stage-8 conclusions are preserved verbatim. The validated
reclaim-distance boundary is not re-derived here. H7 stays **AVOID** (fading H7
is REJECTED, Stage-8). H1_CONT stays **research-only** and CRUDEOIL-only.

---

## 1. Files

### New
| file | role |
|---|---|
| `app/orderflow/h1h7_state.py` | the engine. Pure/deterministic. Self-contained — re-implements the frozen Stage-3/6/7 rules verbatim; **does not import `scripts/*` or read `oi_dashboard`**. Reads bars only via `market_hub.session_bars`. |
| `tests/test_orderflow_h1h7_state.py` | 42 unit tests — one per deterministic rule + look-ahead + no-premium + no-order + unobservable + shadow-CSV. |
| `tests/test_orderflow_h1h7_replay.py` | 8 tests — chronological causal replay on every real captured session + parity against `data/orderflow_stage7_events.csv`. |
| `backend/ORDERFLOW_H1H7_INTEGRATION.md` | this document. |
| `data/orderflow_h1h7_shadow.csv` | append-only shadow event log the engine writes (created on first classified event; git-ignored data dir). |

### Modified (additive only)
| file | change |
|---|---|
| `app/orderflow/service.py` | `+import h1h7_state`; `+h1h7_state(...)` service fn (same 30 s TTL cache pattern as the other order-flow reads). |
| `app/orderflow/api.py` | `+GET /api/orderflow/h1h7-state` on the **existing** `router` (no new router, so **`app/main.py` is not touched**). |
| `frontend/index.html` | `+`"Market Structural State — H1/H7" panel inside the existing `#view-orderflow`. Read-only: no button that triggers anything server-side beyond the GET. |
| `frontend/static/js/app.js` | `+ofRenderH1H7()`; the existing `loadOrderflow()` `Promise.all` gains one GET. |
| `frontend/static/css/style.css` | `+`6 rules for the panel/legend. |
| `frontend/tests/render_smoke.test.js` | `+`fixture + 5 assertions for the panel. |

**Not touched:** any trading engine, `app/execution/*`, `app/autoscalp/*`,
`app/scalper.py`, `runtime.py`, order path, `app/db.py` (no schema change),
`premium_walk.py`, `backtest.py`, `notify.py`, `main.py`, any AutoScalp config.

---

## 2. Research finding → mathematical rule → application state → action

`disp_atr = |close − open| / ATR(14)`. `reclaim_dist` = max over T+1..T+3 of how
far a **completed** close sits back through the broken level `L`, ÷ spike range.
`acc2` = 2 completed closes hold beyond `L` **and** the spike bar closed beyond
`L`. `n1_agree` = reaction candle (T+1) close-vs-open agrees with the spike
direction. `available_R` = |spike close − nearest opposing structural level| ÷
|spike close − (spike extreme ∓ 3% pad)| — **`None` when no opposing level
exists** (never fabricated).

| # | research finding (Stage) | deterministic rule | application state | action | research_status |
|---|---|---|---|---|---|
| 1 | Abnormal displacement precedes every event (S3) | range percentile ≥ **0.90** vs prior completed bar ranges (≥12 required) | else → `NEUTRAL` (not logged) | NO_ACTION | — |
| 2 | The move must break structure to matter (S3/S6) | nearest prior structural level `L` displaced by the spike bar; none → | `SPIKE_NO_LEVEL` | NO_ACTION | NO_ESTABLISHED_EDGE |
| 3 | **Reclaim distance boundary** — continuation collapses monotonically past the knee; universal (S7); fading it is REJECTED (S8) | reclaimed within T+1..T+3 **and** `reclaim_dist > 0.60` | `H7_TRAP` | **AVOID** | SUPPORTED |
| 4 | Graded boundary, conservative lower band (S7) | reclaimed **and** `0.28 < reclaim_dist ≤ 0.60` | `H7_LEANING_TRAP` | **AVOID** | SUPPORTED |
| 5 | Below the knee the event is genuinely ambiguous, **not** a trap (S7) | reclaimed **and** `reclaim_dist ≤ 0.28` | `AMBIGUOUS` | NO_ACTION | NO_ESTABLISHED_EDGE |
| 6 | H1 continuation gate — **Stage-7 REFINED**: body_frac rejected as a discriminator, `disp_atr` is the form; `available_R ≥ 1` is a real interaction gate (S7) | not reclaimed **and** `acc2` **and** `n1_agree` **and** `disp_atr ≥ 1.0` **and** `available_R ≥ 1.0` | `H1_CONT` (CRUDEOIL) / `H1_CONT_OBSERVE` (else) | **CONTINUATION_CANDIDATE** (CRUDEOIL, research-only) / NO_ACTION | RESEARCH_ONLY_PROMISING / **NOT_VALIDATED** |
| 7 | The H1 edge is DIRECTION × ROOM, not pure direction (S7) | gate matched but `available_R` is `None` or `< 1.0` | `H1_CONT_BLOCKED` | NO_ACTION | NO_ESTABLISHED_EDGE (reason names the deficit; `None` → "UNOBSERVABLE … not fabricated") |
| 8 | Weak acceptance ≠ participation (S6) | not reclaimed, `acc1` **and** `n1_agree`, refined gate not met | `H1_WEAK` | NO_ACTION | NO_ESTABLISHED_EDGE |
| 9 | Everything else | — | `AMBIGUOUS` | NO_ACTION | NO_ESTABLISHED_EDGE |

**H1_CONT gate = `disp_atr >= 1.0`** (Stage-7 refined). `body_fraction` is
computed and displayed on every event as `body_fraction` + a
`body_fraction_note` that says "informational only — NOT a classifier gate",
and **never enters any branch condition**.

Per-symbol: `H1_CONT_PROMISING_SYMBOLS = {"CRUDEOIL"}`. NIFTY and NATURALGAS
that match the full gate are emitted as `H1_CONT_OBSERVE` / `NO_ACTION` /
`NOT_VALIDATED`. Nothing is ever `PROVEN` — `RESEARCH_STATUS_LEGEND["PROVEN"]`
literally reads "Not used."

---

## 3. Named deterministic functions (as requested)

`detect_abnormal_spike` · `calculate_spike_range` · `calculate_reclaim_distance`
· `classify_h7_trap` · `detect_n1_agreement` · `calculate_available_R` ·
`classify_h1_continuation` · `classify_market_state` · plus `classify_session`
(the read-only per-session driver) and `append_shadow_rows` (the append-only CSV
writer with on-disk dedup).

Every helper (`_atr`, `spike_percentile`, `_classify_spike`, `_levels`,
`_broken_level`, `_n1`, `_reclaimed_by`, `_avail_R`, `_dev_va`) is copied
**verbatim** from `scripts/orderflow_event_anatomy.py` /
`orderflow_continuation_trap.py` / `orderflow_entry_timing.py` /
`orderflow_stage3_validation.py` / `orderflow_stage6.py`, kept local so the
engine is standalone and its behaviour is pinned by the unit tests.

---

## 4. API / dashboard

`GET /api/orderflow/h1h7-state?symbol=CRUDEOIL&date=2026-09-04[,…]&only_last=false`

```jsonc
{
  "symbol": "CRUDEOIL", "sessions": ["2026-09-04"], "tf": "5m",
  "mode": "SHADOW_OBSERVATION", "live_trading": false,
  "bar_count": 175, "eligible_bars": 164,
  "summary": {"H7_TRAP": 6, "H7_LEANING_TRAP": 2, "AMBIGUOUS": 12, "H1_WEAK": 4,
              "H1_CONT": 2, "H1_CONT_BLOCKED": 3, "SPIKE_NO_LEVEL": 7},
  "events": [
    {
      "symbol": "CRUDEOIL", "timestamp": "2026-09-04T…Z", "mode": "SHADOW_OBSERVATION",
      "live_trading": false, "spike_direction": "SHORT", "spike_range": 40.0,
      "range_pctile": 0.97, "range_x": 3.2, "atr": 12.4,
      "broken_level": 8626.0, "broken_level_kind": "prior_bar_low",
      "reclaim_distance": 24.0, "reclaim_distance_ratio": 0.60, "reclaimed_within_3": true,
      "available_R": null, "n1_agreement": false, "acc1": false, "acc2": false,
      "disp_atr": 2.1, "body_fraction": 0.55,
      "body_fraction_note": "informational only -- NOT a classifier gate (Stage-7)",
      "state": "H7_TRAP", "action": "AVOID", "research_status": "SUPPORTED",
      "reason": "abnormal short spike broke prior_bar_low 8626.00; a completed close returned 24.00 pts back through L = 0.60x spike_range (> 0.60 knee) -> hard trap. AVOID -- do not buy the break. Fading H7 is REJECTED (Stage-8).",
      "unobservable_orderflow": ["aggressor_volume","trade_delta","bid_ask_imbalance","depth_imbalance","absorption","footprint_concentration","iceberg_liquidity","true_constituent_causation"],
      "unobservable_note": "DATA NOT AVAILABLE -- no aggressor-classified tick or L2 depth feed exists in this environment (see backend/ORDERFLOW_STAGE9_L2_RESEARCH.md). …"
    }
  ],
  "research_status_legend": { "SUPPORTED": "…", "PROVEN": "Not used. …" }
}
```

Each classified event is also appended (once — on-disk dedup on
`(symbol,timestamp,state)`) to `data/orderflow_h1h7_shadow.csv` — the audit
trail. No DB write.

Dashboard: **"Market Structural State — H1/H7"** panel in the Order Flow view,
badge `SHADOW / OBSERVATION`, a per-event table (Time, Spike, Broken level,
Reclaim dist, avail R [`UNOBSERVABLE` when `None`], N1, disp/ATR, body frac
[marked *info*], State, Action, Research), and a research-confidence legend
(SUPPORTED / RESEARCH_ONLY_PROMISING / NOT_VALIDATED / NO_ESTABLISHED_EDGE /
UNOBSERVABLE / PROVEN=not used).

---

## 5. Validation

- **Full backend suite:** `cd backend && venv/bin/python -m pytest -q` →
  **614 passed** (baseline 564 + 50 new; 0 regressions).
- **Frontend render smoke:** `node frontend/tests/render_smoke.test.js` → OK
  (5 new panel assertions).
- **No look-ahead:** `test_no_lookahead_full_vs_truncated_identical`,
  `test_event_not_emitted_until_three_forward_bars_complete`, and
  `test_replay_is_causal_on_every_real_session` (every event on every real
  captured CRUDEOIL / NIFTY / NATURALGAS session re-classified from bars
  truncated to `event_bar + 3` → byte-identical dict).
- **Research parity:** `test_h7_band_parity_with_stage7_events_csv` — of the
  overlapping reclaim events between the engine and `orderflow_stage7_events.csv`,
  **87 / 87 (100 %)** fall in the H7 band the CSV's `reclaim_dist_spk` implies;
  **0 / 92** Stage-7 `H7`-labelled rows come back as `CONTINUATION_CANDIDATE`.
- **H1 gate:** `test_h1_gate_uses_disp_atr_threshold` (0.999 → not H1, 1.0 → H1)
  + `test_classify_h1_continuation_pure`.
- **body_fraction informational only:** `test_body_fraction_never_gates_the_state`
  (0.05 … 0.95 across the old 0.55 line — state unchanged),
  `test_body_fraction_low_still_h1_when_disp_atr_passes`.
- **H7 → AVOID always:** `test_h7_states_always_map_to_avoid` (ratios 0.29 /
  0.60001 / 1.5 / 5.0).
- **AMBIGUOUS → NO_ACTION:** `test_shallow_reclaim_is_ambiguous_no_action`,
  `test_else_branch_is_ambiguous_no_action`.
- **available_R < 1.0 → NO_ACTION:** `test_available_r_below_one_blocks_to_no_action`;
  `None` → `test_available_r_none_is_unobservable_not_fabricated`.
- **NIFTY / NATGAS stay NOT_VALIDATED:**
  `test_nifty_natgas_h1_continuation_stays_not_validated`.
- **CRUDEOIL stays research-only, never PROVEN:**
  `test_crudeoil_h1_continuation_is_research_only_never_proven`,
  `test_research_status_legend_has_no_proven_edge`.
- **No option-premium inference:**
  `test_no_option_premium_or_order_semantics_in_output`,
  `test_engine_module_does_not_import_premium_or_execution`.
- **No order / directional-instruction semantics:**
  `test_engine_never_emits_a_directional_trade_instruction`,
  `test_only_three_actions_can_ever_be_emitted` (actions ⊆
  {AVOID, NO_ACTION, CONTINUATION_CANDIDATE}).
- **Unobservable stays unobservable:**
  `test_every_state_carries_the_unobservable_marker` (the 8-item list on every
  event; none of those names is ever a numeric field).
- **Production untouched:** `curl -s localhost:7060/api/health` →
  `{"status":"ok","live_trading":false,"paper_mode":true}`; `git diff --stat`
  shows no change to `main.py`, `app/execution/*`, `app/autoscalp/*`,
  `app/scalper.py`, `app/db.py`, or any order path.

---

## 6. What could NOT be validated / carried limitations

1. **Sample.** Only 3–4 captured sessions per symbol (2026-09-01…04), one
   regime. The engine is deterministic and its rules are frozen from the
   Stage-6/7 chronological splits, but the *shadow output itself* has no
   fresh out-of-sample confirmation. Nothing here is `PROVEN`.
2. **Order-flow internals.** aggressor volume, trade delta, bid-ask imbalance,
   depth imbalance, absorption, footprint concentration, iceberg liquidity,
   true constituent causation — all `UNOBSERVABLE`; no tick / L2 feed exists
   (see `ORDERFLOW_STAGE9_L2_RESEARCH.md`). Never estimated. In particular
   **there is no "200 % / 2:1 order-flow imbalance" rule in the live path** —
   the 2:1 CE/PE-volume proxy lives only in `scripts/orderflow_stage4.py` /
   `stage5.py` and is `REJECTED`; `smart_money.py`'s `volume_mult` (default 2.0)
   is a total-bar-volume spike filter, not a buy/sell imbalance. Full audit:
   `ORDERFLOW_STAGE8_H7_FADE.md` §H.1.
3. **`available_R`** is `None` on fresh-extreme breakouts (nothing structural
   left in the direction of travel). Those events are `H1_CONT_BLOCKED` /
   `NO_ACTION` — the engine does not guess a target.
4. **Option-premium behaviour** is deliberately not modelled. The Stage-4
   finding stands: the underlying structural edge does not survive ATM option
   spread + theta. The engine classifies the underlying only and makes no
   option statement.
5. **CRUDEOIL H1_CONT** is `RESEARCH_ONLY_PROMISING`, not an edge to act on —
   small, holds on the spent Stage-6 holdout only, and does not clear costs on
   the option.
6. **`_dev_va` cadence.** The value-area levels are recomputed every 3 bars
   from bar 15 (matching Stage-3 `HSess`); between refreshes they are carried
   forward. This is the frozen research behaviour, not a new choice.
7. **Parity scope.** The Stage-7 CSV also contains `oi_dashboard` sessions
   (Jul–Aug) that `market_hub` does not hold; parity is checked only on the
   overlapping Sep 2026 zerohero sessions (87 reclaim events).

---

## 7. Next step

Unchanged from `ORDERFLOW_STAGE9_L2_RESEARCH.md`: acquire a real
aggressor-classified tick + full-depth L2 stream for CRUDEOIL / NIFTY futures
(≥ 3 months, ≥ 2 regimes), then re-express the H1 "ignition" state with real
delta / imbalance / absorption and re-run Stages 6–8 against a fresh holdout.
Until then this shadow engine is a *describe-and-log* layer only — it is not to
be promoted to a signal without that evidence and explicit approval.
