# HCS (High-Confidence Signal) Engine — inspection report + design

**Status: SHADOW / RESEARCH. Read-only. Not wired into any live decision path.**
`live_trading` stays `false`. Nothing in `app/engines/*`, `app/autoscalp/*`,
`app/backtest/calibration.py`, `app/orderflow/h1h7_state.py`, execution, tests or
cron is modified.

---

## 1. What already exists (inspection)

The scalping system **already implements ~85 % of the 16 requested modules**,
running live through one function: `app/engines/scalp_strategy.decide_from_context`
(called by `app/autoscalp/runner.py:625` via `asyncio.to_thread`, and by
`app/backtest/runner.py`). It produced today's 8 AUTOSCALP signals.

| # | requested module | already in the codebase? | where |
|--:|---|---|---|
| 1 | Abnormal spike | **yes** | `orderflow/h1h7_state.detect_abnormal_spike` (range pctile ≥ 0.90); `state_classifier` range/ATR component |
| 2 | Spike reaction / rejection | **yes** | `state_classifier` price-action + wick components; `h1h7_state._classify_spike` (bullish/bearish rejection) |
| 3 | Breakout + retest | **yes** | `state_classifier` `retest` component (weight .04) + `false_risk` "failed retest" flag |
| 4 | VWAP + EMA structure | **yes** | `sr_engine` `vwap_status`/`vwap_reason`; `signal_engine._vwap/_ema_series`; `state_classifier` `vwap` component |
| 5 | Momentum (RSI/MACD/ROC/ADX) | **partial** | `signal_engine._rsi/_adx`, `state_classifier` ROC (`roc_pct`), `_htf_trend`. **No MACD, no momentum *acceleration*** |
| 6 | Relative volume expansion | **yes** | `state_classifier._vol_ratio` (bar vol / 10-bar SMA); component `volume` (weight .12) |
| 7 | OI buildup/unwind + CE/PE dominance | **present but DEAD** | `state_classifier._oi_at` + `oi_options_engine` + `oi_math.max_pain`. **Audit 2026-09-05: the `oi` component is always 0 in live** — a real gap |
| 8 | Dynamic S/R | **yes** | `sr_engine.compute_sr` (zones + strength + GEX), `turning_point_engine._pivots` |
| 9 | Market regime detection | **yes** | `regime_mtf.detect_regime` → regime + confidence |
| 10 | MTF alignment (1/3/5/15m) | **yes** | `regime_mtf.mtf_alignment` → alignment + magnitude + `conflict`; MTF gate in `decide_from_context` (opposing HTF blocks) |
| 11 | Liquidity / spread filter | **partial** | `option_engine` spread/quality gate on the *option*; `data_quality.py`. **No index/underlying bid-ask filter** (quote_snapshots has it, unused) |
| 12 | Exhaustion detector | **partial** | `false_risk` "momentum divergence" / "liquidity sweep". **No dedicated RSI-extreme + range-climax + volume-climax exhaustion** |
| 13 | Fake-breakout detector | **yes** | `state_classifier._false_risk` (wick_only / reclaimed / low-vol / failed-retest / sweep / divergence) → `LIKELY_FALSE` forces `NONE` |
| 14 | Absorption detector | **NO — UNOBSERVABLE** | needs aggressor-classified L2; established repeatedly this session (`ORDERFLOW_STAGE9_L2_RESEARCH.md`). Cannot be built honestly |
| 15 | Momentum acceleration | **no** | ROC exists; its 2nd derivative does not |
| 16 | Historical similarity / setup memory | **raw material only** | `trade_entry_features` table holds an immutable per-trade feature snapshot + `component_scores` JSON + outcome. No similarity scorer |

**Score → probability, calibration, walk-forward, hard filters — already built:**

- `state_classifier` → **9-component weighted `state_score` 0-100** (`price_action .20, level_strength .16, volume .12, oi .12, momentum .14, vwap .08, atr .08, htf .06, retest .04`), weights "exposed for P5 calibration".
- `backtest/calibration.fit()` → **logistic `p = sigmoid(k·(s−0.5)+b)`** per `regime|signal_type` + global, `_MIN_ROWS = 40`, conservative `_prior` until a curve exists, trained on **disjoint chronological slices** (walk-forward), `reliability_curve` → Brier + ECE. `decide_from_context._score_to_prob` **explicitly does not equate score with probability** ("PHASE 10 — expose HOW the probability was produced").
- **Hard filters already override the score:** `_filters` config `block_regimes:[UNSTABLE]`, `block_signal_types:[RESISTANCE_BREAKOUT]`, `block_tod:[AFTERNOON]`; MTF opposing-HTF gate; `false_risk == LIKELY_FALSE` forces `NONE`; `ev_gate` RR/EV veto; `effective_confidence` can only *lower* confidence given `data_quality` + `calibration_status`.
- **Output contract already matches the ask:** `{decision: BUY_CE|BUY_PE|NO_TRADE|WATCH, signal_type, direction, entry, stop_loss, target_1, target_2, signal_score, probability, confidence, ev_r, rr, regime, mtf_alignment, false_risk, support/resistance(+strength), vwap_status, gex_*, atr, momentum, component_scores{…}, reason}`.

## 2. The data reality (the honest limiter)

Everything this session established still holds:

| need | have |
|---|---|
| calibration outcomes | **92 resolved AUTOSCALP paper trades** (44 W / 36 L / 12 flat), **2026-08-31 … 2026-09-07 = ~6 sessions, one week, one regime**. `_MIN_ROWS=40` → the *global* curve fits; per-`regime\|type` curves mostly don't; **not a real walk-forward** |
| aggressor / L2 / absorption | **none** — module 14 is `UNOBSERVABLE`, not "todo" |
| index bid-ask / spread history | `quote_snapshots` has it for **~3–4 futures sessions** only |
| OI per strike | `quote_snapshots` options (Sep 2–4) + `oi_dashboard` (Jul–Aug) + Upstox expired-options (NIFTY, Jun–Sep). The live `oi` component being **0** is a wiring bug, not a data gap — fixable |
| multi-year underlying | Kaggle + Upstox NIFTY **5m, cash index, no volume** (proxy only) |

**Conclusion:** a *production-grade, fully regime-calibrated, walk-forward-validated*
engine is **not achievable now** — the outcome history is one week in one regime.
What is achievable and safe: a **meta-layer that raises the quality bar** on top of
the existing (already-calibrated-as-far-as-data-allows) pipeline, adds the few
genuinely-missing modules, hardens the veto layer, and reports calibration
honestly (`INSUFFICIENT` where it is).

## 3. What will be reused / changed / added

| action | item | why |
|---|---|---|
| **REUSE unchanged** | `decide_from_context` + all `app/engines/*` + `calibration.py` + `h1h7_state` | they already are modules 1–13 + calibration + walk-forward + hard filters + the output contract. "Do not rewrite blindly." |
| **REUSE unchanged** | `trade_entry_features` table | module 16 raw material + calibration join |
| **ADD (new pkg `app/hcs/`)** | `evidence.py` — assemble the 16-module evidence view from a `decide_from_context` result + chain + a `quote_snapshots` liquidity read; each module tagged `OK` / `PARTIAL` / `UNOBSERVABLE(reason)` | one place that maps the existing engine outputs onto the requested taxonomy, honestly |
| **ADD** | `score.py` — **HCS quality score 0–100** = transparent weighted aggregation of the *available* evidence, **explicitly a confluence/quality score, NOT the win-probability** (that stays the calibrated `probability`) | the ask: "0–100 calibrated confidence, but do NOT equate score with probability" |
| **ADD** | `filters.py` — **hard-filter veto layer** that can force `NO_TRADE` over any HCS score: bad spread/liquidity, contradictory MTF, exhaustion (RSI extreme + range-climax + vol-climax), fake breakout (`false_risk`), poor RR, major nearby opposing S/R (< k·ATR), noisy/abnormal (low `data_quality`, ATR z-score extreme) | the ask: "Hard filters must be able to override a high score" |
| **ADD** | `memory.py` — **setup-memory / historical-similarity** score from `trade_entry_features` (nearest-neighbour on `state_score`, `regime`, `signal_type`, `tod`, `momentum` → empirical win-rate of the k most-similar past trades, with an `n`/`INSUFFICIENT` guard) | module 16 |
| **ADD** | `engine.py` — `evaluate(sym, …)` → the A+ gate + the full output (decision, `hcs_score`, `calibrated_probability`, confidence, entry/SL/T1/T2, invalidation, `components[]`, `vetoes[]`, `reasons[]`) | the ask's output spec |
| **ADD** | `calibrate.py` + `HCS_CALIBRATION_REPORT.md` — run `calibration.fit()` + `reliability_curve` over the 92 resolved outcomes ⋈ `trade_entry_features`; report global k/b/n, Brier, ECE, per-regime/TOD coverage, and the `INSUFFICIENT` banner | the ask: "calibration reports"; honest about the one-week limit |
| **ADD** | `app/hcs/api.py` — `GET /api/hcs/evaluate` (shadow), `GET /api/hcs/calibration-report` (read-only) | shadow surface |
| **ADD** | `main.py` — one guarded `include_router` block (mirrors `greeks_engine`) | wiring, additive |
| **ADD** | frontend — one **Research → HCS Engine** panel | visibility |
| **ADD** | `tests/test_hcs_engine.py` — veto-override, evidence assembly, score monotonicity, A+ gate, output contract, calibration-report shape | the ask: "with tests" |
| **NOT DONE** | module 14 Absorption | `UNOBSERVABLE` — reported as such, never faked |
| **NOT DONE** | wiring HCS into autoscalp's live loop | out of scope / unsafe on one-week calibration; HCS runs shadow, its verdict is advisory |
| **NOTED** | live `oi` component = 0 bug | flagged for a separate, isolated fix — not bundled into this shadow engine |

## 4. Safety

- HCS **only reads**: it calls `decide_from_context` (already read-only), reads
  `data/chanakya.db` and `data/market_history.db` read-only, and returns a dict.
- It **emits no order and no live signal**; `autoscalp` does not import it.
- `main.py` change = one guarded router block; a failure only logs.
- Backend test suite must stay green; `curl /api/health` → `live_trading:false`.

## 5. Calibration report

See `HCS_CALIBRATION_REPORT.md` (regenerate: `venv/bin/python scripts/hcs_calibrate.py`).
Headline: global logistic fits on n≈80–92, Brier ≈ …, ECE ≈ …, but **6 sessions /
one regime / no true walk-forward → NOT VALIDATED**. Per-regime and per-TOD
curves are `INSUFFICIENT` (< 40 each). The A+ gate is therefore intentionally
conservative and the whole engine is SHADOW until a multi-regime outcome history
exists.
