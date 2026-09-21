# ZeroHero Architecture Inventory — 2026-09-21

Read-only audit against the 27-phase pipeline (DATA → ... → MONITORING) from the
"ZEROHERO — SYSTEMATIC FULL ARCHITECTURE UPGRADE" spec. No code was modified,
deleted, or refactored to produce this file. Every citation below was read
directly from the file named. Classification labels are the spec's own:
KEEP / MODIFY / DEPRECATE / DISABLE / EXPERIMENTAL / DATA_BLOCKED / NOT_YET_SUPPORTED.

## 1. DATA / DATA QUALITY — KEEP

- `app/histcap/store.py` (`HistStore`) — append-only capture into
  `data/market_history.db`, INSERT OR IGNORE dedup, look-ahead-safe reads
  (filters on exchange timestamp, not received timestamp).
- `app/histcap/integrity.py` — real per-snapshot quality checks:
  `candle_check()`, `quote_check()`, `greek_check()`, `monotonic_flag()`,
  `gap_flag()`. This is the spec's Phase 3 "data quality engine" — already
  exists, not a `data_status`/`freshness`/`quality_score` single struct as
  literally specced, but the same checks (stale/partial/missing) are real
  and already gate what gets written.
- `app/orderflow/depth.py` — every read honestly labeled `available: true/false`
  with a `reason` when false ("no L2 capture row for this token/time"), never
  fabricates a missing snapshot as 0.

## 2. FEATURE ENGINEERING — KEEP (deterministic formula), MODIFY (probability, see §11)

- `app/engines/state_classifier.py:26` — the real 9-component weighted formula:
  `_W = {price_action:0.20, level_strength:0.16, volume:0.12, oi:0.12,
  momentum:0.14, vwap:0.08, atr:0.08, htf:0.06, retest:0.04}` feeding
  `state_score`. This is the live, production formula — untouched today,
  confirmed via direct read, not the spec's own hypothesized formula.
- `app/greeks_engine/` — real Black-Scholes Greeks (not approximation tables).
- `app/engines/sr_engine.py` — real S/R zone clustering + OI-wall detection +
  GEX profile (own Black-Scholes IV solver, `_bs_price`/`_bs_gamma`/`_solve_iv`).
- Bollinger Bands: **NOT_YET_SUPPORTED** — confirmed absent anywhere in this
  codebase (already found today in `data/research/ta_options_pdf_upgrade/VERDICT.md`;
  backtested as a candidate, PROMISING but not GO, not wired anywhere).

## 3. MARKET REGIME — KEEP

- `app/engines/state_classifier.py` — regime output (BULLISH/BEARISH state types).
- `app/behavior_engine.py:61` (`analyze_behavior`) — buckets the deterministic
  engine's own `sig["regime"]` into TREND/RANGE/CHOP, and `trend` into
  UP/DOWN from the same source (`"UP" if "UP" in mtf_regime else ...`) — i.e.
  regime is derived from information already available at signal time, no
  look-ahead.

## 4. MARKET STRUCTURE / OPTION STRUCTURE — KEEP

- `app/sr_dynamic/` (`engine.py`, `live_state.py`, `signal_confirm.py`,
  `mtf_confirm.py`, `breakout.py`, `pivots.py`, `clustering.py`, `touches.py`,
  `visits.py`) — a live-state confirmation layer built ON TOP of
  `sr_engine.compute_sr()` (imports it directly), wired into
  `scalp_strategy.py:480` via `refresh_and_store()` +
  `evaluate_sr_confirmation()`. Not a duplicate of `sr_engine.py` — one
  computes zones, the other tracks live touches/confirmation state against
  those zones. Both actively imported by `scalp_strategy.py`.
- `app/optionchain/structure.py` — `_max_pain_block`, `_pcr_block`,
  `_walls_block`, `_skew_block`, `_iv_rv_block`, `_gex_block`, all real,
  computed in `analyze()`. **`_iv_rv_block` is MODIFY** — it's computed for
  the option-chain display but never read by `scalp_strategy.py` as a gate
  (confirmed via grep — zero references to `iv_rv` outside `optionchain/`).
  Already identified and backtested today (VERDICT.md): NO-GO as an entry
  gate on real data, threshold overfits train, n collapses on swap.

## 5. ORDER FLOW / DEPTH — KEEP (as informational), DISABLE (as a gate, by design)

- `app/orderflow/depth.py` — resting order-book imbalance from L2 SnapQuote
  captures (NIFTY/CRUDEOIL/NATURALGAS only — no L2 capture for
  BANKNIFTY/SENSEX, confirmed live today via a real `snapshot_for_symbol`
  call returning `available:false`). Explicitly NOT trade-level aggressor
  flow (no tick data exists anywhere in this codebase) and explicitly
  labeled as such in its own module docstring. A passive-book imbalance GATE
  was already tested and rejected at feasibility (memory:
  `orderflow-l2-imbalance-gate.md` — 3 same-week sessions, MAE-neutral, 200%
  threshold not special). Currently used only as an **informational** field
  in the Groq/OpenAI verification payload (`app/ai/behavior_ai.py`) and in
  `app/ai/fusion.py`'s `orderflow_conflict` downgrade-only rule — this is a
  real, live contradiction check (see §9), not a signal generator.

## 6. SETUP DETECTION — KEEP (implicit), NOT formally separated

- `app/engines/scalp_strategy.py::decide_from_context()` conflates
  setup-detection and entry into one function (`signal_type` in
  `{"SUPPORT_REVERSAL","RESISTANCE_BREAKOUT","SUPPORT_BREAKDOWN","RESISTANCE_REVERSAL"}`
  plus a `WATCH` intermediate state when confidence is LOW — see line ~496-508:
  `if confidence == "LOW" ... return {**ctx, "decision": "WATCH", ...}`).
  A `WATCH` state already exists as a soft setup/no-entry distinction, but
  there's no formal `NO_SETUP → SETUP_DETECTED → WAIT_CONFIRMATION →
  ENTRY_VALID` state machine as literally specced. Real but implicit —
  MODIFY if a formal state machine is ever wanted, not currently a gap that
  blocks anything.

## 7. CONFIRMATION — KEEP (production), EXPERIMENTAL (research)

- Production: `sr_dynamic.signal_confirm.evaluate_sr_confirmation()` (real
  CONFIRM/REJECT verdict feeding a confidence step in `scalp_strategy.py`),
  plus the AI/Groq shadow-verification layer (`app/ai/behavior_ai.py` +
  `app/ai/fusion.py`) which independently validates/downgrades.
- `app/reverse_engineering/confirmation.py` — a 9-group (price
  action/volume/OI/premium/VWAP/S-R/orderflow/volatility/regime)
  corroboration engine requiring ≥3 independent EVIDENCE_FOR groups to reach
  CONFIRMED. Built earlier TODAY. **EXPERIMENTAL** — fully disconnected,
  never imported by `scalp_strategy.py`/`autoscalp.runner`/`ai.shadow`
  (confirmed: only docstring mentions, no real imports), no live/paper
  wiring, n=5 known research events only.

## 8. CONTRADICTION CHECK — KEEP

- `app/ai/fusion.py:123` — `if ai_result.get("orderflow_conflict") is True:
  state = _weaken(state, 1)` — a real, live, independent (non-`elif`)
  contradiction rule: if the AI's real-time orderflow read disagrees with
  the deterministic direction, the state weakens one step, and stacks with
  an existing HIGH-risk/profile-mismatch weaken. Verified live in production
  today: a real NIFTY decision at 2026-09-21T07:47 showed
  `orderflow_conflict:true` with `reason_codes:["BEARISH orderflow
  contradicts bullish breakout expectation"]`, independently cross-checked
  against a real `depth.snapshot_for_symbol('NIFTY')` call showing genuine
  `orderflow_state:BEARISH` at the same timestamp — not fabricated.

## 9. PROBABILITY — MODIFY, §10/11 below

- `app/engines/scalp_strategy.py:172` (`_score_to_prob`) and `:227`
  (`effective_confidence`) — raw `signal_score` is converted into a
  probability via calibration data (`calib`), not used as a raw score
  directly. So the spec's "don't treat raw score as probability" distinction
  formally exists in code.

## 10. REGIME-CONDITIONED PROBABILITY — PARTIAL / DATA_BLOCKED

- `_score_to_prob(signal_score, calib, *, regime, signal_type)` already takes
  `regime` and `signal_type` as parameters — calibration IS regime-aware in
  signature. Whether the underlying calibration TABLE has sufficient
  per-regime sample size is exactly the open question in §11's known issue
  (K8) — memory records a specific subgroup gap: "TRENDING_UP subgroup
  +29.4pp gap" (calibration-overconfidence-k8.md), i.e. the regime-split is
  real but at least one regime bucket is measurably miscalibrated.

## 11. PROBABILITY CALIBRATION — **DATA_BLOCKED / MODIFY-NEEDED (known, unfixed)**

- `app/autoscalp/calibration_report.py:51` (`calibration_report()`) — real
  reliability/ECE computation (`_reliability()` at line 33, real 10-bin
  calibration curve).
- Memory `calibration-overconfidence-k8.md` (re-checked 2026-09-11, n=122):
  **ECE 0.185, Brier 0.302, profit factor 0.618, expectancy -1.04 pts/trade,
  reliability INVERTS at high confidence (0.8-0.9 confidence bin = 12.5%
  actual win rate)**. This is a real, previously-identified, still-unfixed
  defect — the autoscalp probability output overstates win-rate, most
  severely in the TRENDING_UP regime subgroup (+29.4pp gap). No fix has been
  applied (read-only finding at the time, per memory). This is the single
  most concrete, evidence-backed MODIFY-NEEDED item in the entire audit —
  everything downstream of `_score_to_prob()` (trade quality gate, R:R
  sizing) inherits this miscalibration whenever raw confidence is displayed
  or used as if it were a validated probability.

## 12. MFE / MAE — KEEP (built TODAY)

- `app/ai/shadow_outcome.py::check_outcome()` — real forward-outcome tracking
  (TARGET_HIT/SL_HIT/OPEN/TIMED_OUT_NO_HIT/INSUFFICIENT_DATA) walking real
  captured option-premium snapshots after a signal, first-touch wins, real
  MFE/MAE computed from real `ltp` values, never fabricates a missing
  snapshot. `outcomes_report()` + `GET /api/ai/shadow-outcomes` for review.
  No look-ahead into signal generation (this is post-hoc review only, never
  read by `decide_from_context()`).

## 13. RISK/REWARD ENGINE — KEEP

- `app/engines/option_engine.py:216` (`ev_gate`) — real EV/cost/R:R gate,
  takes `prob, entry, stop_loss, target_1` and the real cost model.
  `app/engines/scalp_strategy.py:444-447` applies `est_cost_r` (0 for NIFTY
  weeklies by explicit design comment, set for other instruments) against
  1R risk before the gate decision.

## 14. TRADE QUALITY GATE — KEEP

- `app/engines/scalp_strategy.py::decide_from_context()` is itself the
  composite gate: blends `0.62*state_score + 0.24*option_quality +
  0.14*mtf_magnitude`, then applies `ev_gate`, the S/R confirmation check,
  and (in shadow mode only, not gating live) the AI fusion layer. A `LOW`
  confidence result returns `WATCH`, not a trade. No single indicator can
  alone produce a BUY/SELL.

## 15. PAPER TRADE — KEEP

- `app/autoscalp/` — the live paper-trading engine (already running,
  `paper_mode:true` confirmed live today via `/api/health`). Records
  timestamp/instrument/direction/entry/stop/target per trade.
- `app/reverse_engineering/ml_prep.py::check_training_readiness()` — a real
  `NOT_READY/INSUFFICIENT_SAMPLE` gate (n=5 known signals vs 100-event floor),
  confirmed no `xgboost.fit()` call exists anywhere in that module.

## 16. OUTCOME ANALYSIS — KEEP (built today)

- `app/ai/shadow_outcome.py` (see §12) is also the outcome-analysis layer —
  same module serves both MFE/MAE and outcome classification.

## 17. BACKTEST — KEEP

- `app/backtest/runner.py::run_backtest()` — the canonical engine, calls the
  LIVE `decide_from_context()` directly against real data
  (`/root/oi_dashboard/oi_history.db`), chronological train/test split
  (`run_backtest(symbol, *, train, test, ...)`), real metrics
  (`_metrics()`, `_drawdown()`, `_max_consec_losses()`, `_group_stats()`).
  `run_ablation()` at line 240 — a real feature-ablation harness already exists.

## 18. WALK-FORWARD VALIDATION — KEEP (discipline already applied repeatedly)

Not a missing phase — this session's own track record IS the walk-forward
discipline in practice, applied to multiple candidate strategies, each with
a real NO-GO/PROMISING verdict on file:
- `LIQUIDITY_SWEEP_FINAL_REPORT.md` (repo root) — NOT READY (0/38 features
  joint AUC 0.54).
- `data/research/ssl_hybrid_pro/SSL_HYBRID_PRO_FINAL_VERDICT.md` — NO-GO
  across NIFTY/BANKNIFTY (2 independent periods each) + NATURALGAS.
- `data/research/tri_compare/TRI_COMPARE_RESULT.md` — 3-engine comparison,
  all NO-GO.
- `data/research/trend_swing/TREND_SWING_RESULT.md` — NO-GO, 0/6
  walk-forward folds.
- `data/research/ta_options_pdf_upgrade/VERDICT.md` — Bollinger PROMISING
  (survived a train/test swap), IV-vs-RV NO-GO — built earlier today.
- StochRSI+Supertrend and the NIFTY weekly Iron Condor engines were tested
  NO-GO and their code was subsequently removed per this session's own
  cleanup phase (memory: `stochrsi-supertrend-nogo.md`,
  `nifty-weekly-ic-intraday-nogo.md`) — no source file remains to cite; the
  memory record is the surviving evidence of the NO-GO finding, this is
  disclosed rather than silently omitted.

## 19. MONITORING — EXPERIMENTAL / DISABLE (built, not gating)

- `app/structural_break/` — `drift.py`, `feature_drift.py`,
  `prediction_drift.py`, `performance_monitor.py`, `break_score.py`,
  `evaluator.py`, `adaptation.py`, `regime_profiles.py`, `audit_log.py`,
  `backtest_compare.py`, `shadow.py`. Confirmed via grep: **not imported by
  `app/engines/scalp_strategy.py` or `app/autoscalp/runner.py`** — i.e.
  genuinely "dark" (built, tested, never gates a live decision), matching
  memory's own description. `app/telegram_dispatcher.py` does read its
  state as an informational footer on outbound signal messages only.

## Genuine gaps (NOT_YET_SUPPORTED / DATA_BLOCKED), consolidated

1. **Probability calibration is measurably wrong** (§11) — the one
   concrete, evidence-backed defect in this whole audit.
2. **Bollinger Bands** — absent, PROMISING-not-GO candidate on file.
3. **IV-vs-Realized-Vol as an entry gate** — computed, wired nowhere,
   tested NO-GO today.
4. **BANKNIFTY/SENSEX have no L2 order-book capture** — orderflow
   contradiction-check (§8) structurally cannot fire for these symbols
   (confirmed live: `depth.snapshot_for_symbol('BANKNIFTY')` returns
   `available:false`) — this is a data-collection gap, not a logic gap.
5. **No formal SETUP state machine** (§6) — implicit via `WATCH`, not the
   literal `NO_SETUP → SETUP_DETECTED → WAIT_CONFIRMATION → ENTRY_VALID`
   states the spec describes.

## Scorecard

Of the spec's ~19 distinct pipeline concerns mapped above (some phases in
the original 27-phase numbering collapse into the same real component,
e.g. phases 9/10/11 all map to the same calibration code path):
- **KEEP** (real, working, already covers the spec's intent): 14
- **MODIFY** (real but with a known defect or a wiring gap): 3 (§9/§11
  calibration, §4 IV-vs-RV wiring)
- **EXPERIMENTAL** (built, real, deliberately disconnected): 2 (§7
  reverse_engineering confirmation, §19 structural_break monitoring)
- **NOT_YET_SUPPORTED / DATA_BLOCKED**: 2 (Bollinger Bands, BANKNIFTY/SENSEX
  L2 capture)

No component was found that needs DEPRECATE or DISABLE action — nothing
audited here is a duplicate fighting for the same job; `sr_engine.py` vs
`sr_dynamic/` looked like a candidate duplicate but is actually two
distinct layers (zone computation vs. live confirmation state) that both
feed `scalp_strategy.py` today.
