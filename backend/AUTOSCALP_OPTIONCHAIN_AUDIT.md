# AutoScalp × Option-Chain — Audit + Validation + Minimum-Wiring

_2026-09-10. Read-only audit. **No production code was changed.** All claims are
traced to `file:line` in the current tree; the load-bearing ones were
re-verified by hand after the automated trace._

---

## 0. Bottom line (Phase 6 verdicts)

| | Verdict | Why |
|---|---|---|
| **A. Probability calibration** | **NO-GO** | Live calibration fits **and** grades on one unrestricted pool — no train/OOS split anywhere in production. Total resolved LIVE sample n=99; `_MIN_ROWS=40` per `regime\|signal_type` is unreachable so per-regime curves never populate. Backtest OOS yields ~10 closed trades per 8-day fold. There is not enough resolved-trade data to fit **and** validate a calibration under strict walk-forward. The sanctioned config-only haircut (`regime_score_mult`) is already applied and its own simulation shows it cannot calibrate. |
| **B. Option-chain → AutoScalp wiring** | **NO-GO on new wiring** | The causal chain evidence that matters is **already wired** (OI-wall S/R levels + OI-backing strength → `state_score` → probability; CE/PE premium + greeks → `ce_pe_confirmation` / `select_option` quality floor / `ev_gate`; expiry-day runner overrides). PCR, max-pain, GEX and the *distinct* ΔOI-unwinding signal are computed but not gated. Adding them as decision inputs needs strict OOS evidence that they improve the decision — which cannot be produced on this sample. |
| **C. Layer 4 ANN** | **NOT JUSTIFIED** | The problem is not "the existing model is uncalibrated and an ANN would calibrate it" — the problem is **insufficient data everywhere**. The ANN backtest already returned INSUFFICIENT_SAMPLE (`OPTIONCHAIN_ANN_RESULT.md`) on the same class of data. It stays DARK. |
| **D. Production** | **UNCHANGED** | No engine, config, calibration or execution change. Only additive: one audit-lock test file + this document. |

**Production remains unchanged unless OOS evidence supports the change. It does not.**

---

## 1. Current wiring (proven from code)

### 1.1 How the chain reaches the decision

`app/runtime.py:112` `_autoscalp_chain()` → wired as `chain_provider` at
`app/runtime.py:188`. It calls `market_hub.get_chain()` (`app/market_hub.py:342`,
histcap-first + throttled REST fallback) and reshapes each strike to
`{"strike", "ce": {...}, "pe": {...}}` (`runtime.py:142-160`).

`app/autoscalp/runner.py::_evaluate`:
- `:589` `bars = agg.snapshot(now_epoch=self._now())` — point-in-time.
- `:602` `chain = await asyncio.to_thread(self.chain_provider, sym, atm, cfg["strike_window"], smeta["exchange"], emode)` — point-in-time.
- `:624` `decide_from_context(bars, chain, atm=atm, calib=self.calibration(), avg_win=None, avg_loss=None, leg_bars_fn=_leg_fn, tod_bucket=tod, config=strat_cfg)`.

### 1.2 What `decide_from_context` does with `chain` (`app/engines/scalp_strategy.py`)

| use | site | GATE or display? |
|---|---|---|
| `compute_sr(bars, chain=chain, mode="index")` | `:185` | **partial GATE** via score + display |
| `classify(bars, sr, chain=chain)` | `:191` | **partial GATE** via `min_state_score` |
| per-leg `analyse_leg(..., chain=chain)` for CE & PE at ATM | `:286-289` | **GATE** via `ce_pe_confirmation` |
| per-candidate `analyse_leg(..., chain=chain, light=True)` | `:300-301` | **GATE** via `select_option` + `min_option_quality` |
| selected-strike `analyse_leg(..., chain=chain)` | `:309-310` | **GATE** via `ev_gate` (entry = leg LTP) |

**`compute_sr` (`app/engines/sr_engine.py`):**
- `mode=="index" and chain` adds S/R candidates: `oi_wall_ce`/`oi_wall_pe` (weight `0.5 + 0.8·OI_share`, `:138-143`) and `oi_write_ce`/`oi_write_pe` (weight 0.6, requires `oi_chg > 0`, `:145-151`). These **move where support/resistance land**.
- Strength terms `oi_backing` (0.12) + `oi_change_backing` (0.05) (`_strength`, `:209-216`) — up to ~17% of a zone's 0-100 strength.
- Zone strength → `sr["levels"]` → `classify` `level_strength` component (16% of `state_score`) → `state_score` is 62% of `blended` (`scalp_strategy.py:318`) → `_score_to_prob`. **So chain OI reaches the probability, indirectly.**
- **PCR / max-pain: `compute_sr` never computes them.**
- **GEX** (`_gex_profile`, `:351-443`; scalars `:550-552`): explicit — `:287-294` "is NOT fed into `_candidates`/`_strength`", `:550` "GEX scalars … do NOT gate anything (v1a)". Ends up in `ctx`/output/snapshot only.
- `_oi_wall_diag` (`sr_diag.oi_walls`, `:237-276`): `:240` "does not feed the score", `:275` "illustrative only".

**`classify` (`app/engines/state_classifier.py`):**
- `_eval_break`/`_eval_reversal` call `_oi_at(chain, lvl, "ce_write"/"pe_write")` (`:189,262`); `_oi_at` reads `near.ce.oi_chg`/`.oi` (`:83-100`) → `oi` component, weight **0.12** (`_W`, `:29`).
- `state_score < min_state_score` (default 45.0, `:343-345`) → `state="NONE"` → `decide_from_context` returns NO_TRADE (`scalp_strategy.py:215-216`).
- **Caveat:** per `CALIBRATION_OVERCONFIDENCE_AUDIT.md §3`, this `oi` component is empirically **exactly 0.0 across all 99 resolved LIVE rows** — 12% of `state_score` weight has carried zero information; root cause (chain `oi_chg` arriving None/0 vs a wiring defect) is **OPEN**.

**`analyse_leg` (`app/engines/option_engine.py:91-152`)** extracts `ltp, delta, theta, iv, oi, vol_delta` (+ `gamma` in `_translation`). Its `confirm` label gates via `ce_pe_confirmation` (`scalp_strategy.py:290`, CONFLICT/OPPOSING → NO_TRADE); its `final_quality` gates via `min_option_quality` default 45 (`:302-307`); `sel["ltp"]` sets the plan entry for `ev_gate` (`:334-338`).

### 1.3 Expiry-day (`app/autoscalp/runner.py`, not in the engines)

`_chain_is_expiry_day(chain)` (`:298`) → `is_expiry_day` (`:610`) drives:
1. `expiry_day_profile` config merge (`:621`) — tighter T1/T2/SL, **RR floor 1.15 vs 1.3**, higher cost haircut → different `ev_gate` + plan.
2. Entry cutoff 14:15 IST (`:662-666`).
3. `AUTO_ROLL` — skip the 0-DTE contract (`:596`), then `is_expiry_day` forced False.
4. Zero-to-hero far-OTM lottery leg, HIGH-confidence trending only, separate strategy, excluded from calibration + budget (`:683`, `_maybe_open_zth`).

No `dte`/`0dte` branching inside `scalp_strategy` / `sr_engine` / `option_engine` / `state_classifier`. `_gex_profile` uses a fixed `_DEFAULT_T_YEARS` and the runner never passes `cfg["gex"]["t_years"]`, so **GEX is not expiry-aware** — but GEX gates nothing anyway.

---

## 2. Missing wiring

| item | status |
|---|---|
| **PCR** | computed every cycle (`runner._chain_oi_quality:314`), persisted to `live_market_snapshots` / `scalp_signals` / `trade_entry_features`; **never passed to `decide_from_context`** — the decide call (`runner.py:624`) has no `pcr` arg. |
| **Max-pain** | same — `oi_math.max_pain_strike` via `_chain_oi_quality`; logged, never gated. |
| **GEX** (flip / pin / regime_sign / sigma / per-strike profile) | fully computed in `sr_engine._gex_profile`; explicitly excluded from `_candidates`/`_strength` (`:287-294,550`). ctx/snapshot only. |
| **ΔOI unwinding** | only `sign(oi_chg) > 0` (fresh writing / buildup) is used, in 3 places. `_oi_at` clamps to `[0,1]` so **negative ΔOI (unwinding) floors at 0** — unwinding is not distinctly modeled. And the `oi` component is empirically dead (§1.2). |
| **`iv` on the leg** | read in `analyse_leg` **only** to derive an `iv_context` label; that label is not a term in the `quality` sum and gates nothing. Effectively display-only for the decision. `_gex_profile` re-solves ATM IV from LTP and ignores the broker `iv` already on the row. |
| **`vega`** | never referenced in any decision-path module; `trade_features.py:92` (post-hoc snapshot) only. |
| **`bid` / `ask`** | not present in the live chain row at all (`runtime.py:142-160`). |
| **`app/optionchain/` module** (`OptionStructureState`, `qualify`, `get_chain`) | fully isolated. grep of `app/` (ex-`app/optionchain/`, ex-tests) for `app.optionchain` imports → **only hit is `app/main.py:230` mounting the read-only dashboard router.** `runner.py` / `scalp_strategy.py` / `sr_engine.py` / `option_engine.py` / `state_classifier.py` / `app/autoscalp/*` / `app/execution/*` / `app/backtest/*` — none import it. Locked by `tests/test_autoscalp_optionchain_wiring_audit.py`. |
| **Live/replay feature asymmetry** | `option_engine._translation` no-greek fallback reads `leg.get("chg_pct")` (`option_engine.py:84`); `chg_pct` is produced **only by the replay adapter** (`oi_history_adapter.py:174-198`), never by live `_autoscalp_chain`. So live always takes the greeks path; the degraded-greek archive often takes the fallback → **calibration is fit under a different feature regime than live runs under.** |

---

## 3. Exact files / functions changed

**Production engine / config / calibration / execution: NONE.**

Additive only:
| file | change |
|---|---|
| `backend/tests/test_autoscalp_optionchain_wiring_audit.py` | NEW — 8 audit-lock tests: `app.optionchain` not imported by the 16-file decision path; its `__init__` still declares "no order path"; `decide_from_context` returns a safe dict (never a BUY, never an exception) on `None` / `[]` / all-None / malformed chains. |
| `backend/AUTOSCALP_OPTIONCHAIN_AUDIT.md` | NEW — this document. |
| `backend/OPTIONCHAIN_ANN_RESULT.md` | (from the prior task) — the ANN Layer-4 INSUFFICIENT_SAMPLE result. |

---

## 4. Option-chain fields actually consumed by AutoScalp (today)

**Gating the decision (directly or via score/probability):**
- per-strike `ce.oi`, `pe.oi` → OI-wall S/R candidates (`sr_engine._candidates`) + `oi_backing` strength.
- per-strike `ce.oi_chg`, `pe.oi_chg` — **only `> 0`** → `oi_write_*` S/R candidates + `oi_change_backing` strength + `state_classifier` `oi` component (empirically 0.0 in live data).
- ATM `ce.ltp`, `pe.ltp` → `analyse_leg` premium_fit + the plan entry price → `ev_gate` RR/EV.
- ATM/near `ce.delta`, `pe.delta`, `ce.gamma`, `pe.gamma`, `ce.theta` → `_translation` (index-move → option-move), `delta_fit`, `theta_drag` → `analyse_leg.confirm` + `final_quality` → `ce_pe_confirmation` + `select_option` quality floor.
- `ce.oi` + `ce.vol_delta` → `analyse_leg` liquidity term.
- leg `expiry` → `_chain_is_expiry_day` → runner expiry-day profile / cutoff / roll / ZTH.

**Consumed but non-gating (ctx / diagnostics / persisted rows only):**
- `iv` (→ `iv_context` label), `vega` (post-hoc), GEX (flip/pin/regime_sign/sigma + per-strike), `sr_diag.oi_walls` distance-weighted scores, PCR, max-pain, `greeks_source` / `oi_status` / `oi_source` / `oi_timestamp`.

---

## 5. Calibration findings

### 5.1 How the number is built

`_score_to_prob` (`scalp_strategy.py:60-76`): raw 0-100 `signal_score` → `s∈[0,1]` →
- **fixed prior** `1/(1+e^{-(2.3·(s−0.58))})` — never fitted;
- if a curve exists for `regime|signal_type` → `signal_type` → `global`: `1/(1+e^{-(k·(s−0.5)+b)})`.

`blended = 0.62·state_score + 0.24·final_quality + 0.14·min(100, mtf_mag+40)`, then `× regime_score_mult × signal_type_score_mult × tod_score_mult`, clamped (`:318-325`).

**No isotonic / PAV anywhere** in `app/backtest/calibration.py` or `scalp_strategy.py`. The fit (`calibration.fit`, `:64-88`) is OLS-on-logit-of-bucketed-win-rates, `k∈[1,7]`, `b∈[−1.5,1.5]`, per curve `_MIN_ROWS = 40` or the curve is not emitted.

### 5.2 Where it's fitted

- **LIVE — `AutoScalpRunner._maybe_recalibrate` (`runner.py:1017-1031`):** every 900 s, `db.list_scalp_signals(source="LIVE", status="CLOSED", limit=2000)` → **entire resolved history, NO train/OOS split** → `calibration.fit` → written to `autoscalp_calibration`. The grading surface (`GET /api/autoscalp/calibration-report` → `calibration_report._resolved_rows`) reads the **same** unrestricted pool. **Fit and grade on one pool — the methodology defect.**
- **OFFLINE — `app/backtest/runner.py::run_backtest`:** proper chronological TRAIN → `calibration.fit` → freeze → OOS TEST, emits `calibration_reliability` (Brier/ECE/bins) + `by_regime`. **But: a single disjoint split, not a rolling walk-forward; no purge/embargo; different feature regime than live (degraded archive greeks); library+test only, not on any route.** No `walk_forward` / `purge` / `embargo` symbol exists in `app/backtest`.

### 5.3 The documented problem (from `CALIBRATION_OVERCONFIDENCE_AUDIT*.md`)

- n=74 (K8, 2026-09-05): actual win 47.3%, predicted 57.7%, **ECE 0.115**, **Brier 0.282** (> base-rate ~0.25), PF 0.75, **−0.54 pts/trade**. Non-monotonic score buckets (60-70 → 34.8%, worse than 40-50 → 66.7%).
- TRENDING_UP re-run (2026-09-07, n=23): predicted 59.9%, actual **30.4%**, **gap +29.4 pp**, **Brier 0.315** (naive 0.212), **ECE 0.294**, PF 0.50, **−1.31 pts/trade → −EV as traded.**
- `oi` state-classifier component = 0.0 in 100% of rows. Feature↔actual-win correlations all ≈ 0.
- `curves` empty in the live blob → everything scored through one global sigmoid (or the prior).
- **Applied (config-only, `CALIBRATION_TRENDING_UP_HAIRCUT_PROPOSAL.md`):** `regime_score_mult = {"RANGE": 0.7, "TRENDING_UP": 0.80}` in `symbol_profiles` for NATURALGAS / CRUDEOIL / BANKNIFTY / SENSEX (NIFTY frozen → `{"RANGE": 0.7}`). Its own 23-trade simulation: `m` 1.00→0.75 changes nothing (all 23 still taken, PF 0.50); `m ≤ 0.70` drops trades but **worsens** net P&L (drops winners). **"A score-mult haircut cannot calibrate TRENDING_UP."**

### 5.4 This audit's own walk-forward attempt

Anchored TRAIN 2026-07-13..08-14 / OOS TEST 08-17..08-26 on **NATURALGAS** (highest-frequency symbol in the 35-day OI-history archive), replaying `decide_from_context`, `decide_every_sec=30`:
- **TRAIN → 44 calibration samples, `curves = []`** (no `regime|signal_type` cell reached `_MIN_ROWS=40`; only the global sigmoid was fit: `k=1.0, b=−0.218, n=44, win_rate=0.455`).
- **OOS → 3821 decisions, 10 entries, 10 closed trades.**

**10 OOS trades per 8-day fold** is not enough for an OOS Brier/ECE, let alone a NORMAL-vs-EXPIRY split or an A/B/C comparison. NIFTY produced **0** closed OOS trades in a 5-day fold (frozen config is far more conservative). The live pool is n=99, fragmented across 6 regimes × 3 signal types.

**→ There is no dataset — live or replay — on which a calibration can be both fitted and validated under strict walk-forward with a defensible sample. NO-GO.**

---

## 6. Before / after signal quality

No "after". Production is unchanged, so there is nothing to compare. The
audit-lock tests confirm existing behaviour is preserved (§11).

---

## 7. Brier / calibration impact

Zero — no calibration change was made. The evidence for *why not*: §5.3 (live
ECE 0.29 / Brier > base-rate on n≈80, un-fixable by the sanctioned lever) and
§5.4 (10 OOS trades/fold, `curves=[]`).

---

## 8. Normal-day vs expiry-day results

Not separable on this sample (10 OOS NATURALGAS trades total; expiry-day is a
subset of that). Expiry-day *handling* in the runner (§1.3) is intact and
covered by `tests/test_autoscalp.py` (`_expiry_chain_provider`, ZTH, entry
cutoff, AUTO_ROLL). The option-chain view's own expiry-day logic
(`app/optionchain`, `phase`/`G_EXPIRY`/banner) is a **separate, research-only**
path and does not touch AutoScalp.

---

## 9. Walk-forward / OOS methodology used

- Archive: `/root/oi_dashboard/oi_history.db`, `cycles` 2026-07-13..08-28 (35 trading days), opened `mode=ro&immutable=1`.
- Harness: existing `app/backtest/runner.run_backtest` internals (`_train_samples` → `calibration.fit` → freeze → `ReplayHarness.run(_build_decide(calib,…))`), which replays `decide_from_context` over `oi_history_adapter.iter_market_states` in strict `cycles.ts` order.
- `ReplayContext.candles` returns closed past bars only (explicit `break` on the first future bar, `replay.py:162-180`); `_LegCache.fn_at(ts)` truncates option candles to `≤ decision ts` (`backtest/runner.py:36-48`); calibration frozen TRAIN→TEST.
- Split: **single anchored chronological hold-out** (TRAIN 07-13..08-14, OOS 08-17..08-26). No rolling multi-fold, no purge/embargo — because the OOS trade count (10) makes multi-fold pointless.
- **No threshold or curve was fitted or tuned on the OOS window.** The in-sample "A" leg (curve fitted on the OOS trades themselves) was intended purely as an over-fit upper bound; it was not reached before the sample-size finding made the comparison moot.

---

## 10. Leakage audit result

**No future-bar / future-chain / post-entry information enters the signal, in either path.** Detail:

- `decide_from_context` is pure over `(bars_by_tf, chain, atm, calib, avg_win, avg_loss, leg_bars_fn, tod_bucket, config)` — no timestamps, no I/O (`scalp_strategy.py:10-11`). `compute_sr` / `classify` / `analyse_leg` touch only the passed arrays.
- **Live:** bars = `agg.snapshot(now)`, chain = `chain_provider(now)`, leg bars = per-leg `agg.snapshot(now)` — all point-in-time (`runner.py:589,602,552-567`).
- **Replay:** `for state in ad.iter_market_states(...)` chronological; `state["chain"]` is that cycle's contemporaneous snapshot; `decide(state, ctx)` with `ctx.candles` = closed bars ≤ now, `leg_cache.fn_at(state["ts"])` = option bars ≤ ts. Trade sim reads the LOCKED contract's *subsequent* marks — realised outcome, not a signal input (`replay.py:265-342`, `backtest/runner.py:52-61`).
- `tests/test_backtest_no_lookahead_calibration_is_frozen` locks the TRAIN→freeze→TEST order.

**Fidelity flags (not strict leakage):**
1. Live chain carries broker greeks; the replay archive greeks are ~40% NULL, tokens/expiry ~90% NULL, and the replay adds `chg_pct` that live lacks → `option_engine._translation` takes different branches live vs replay → **calibration fit under a different feature regime than live.**
2. `backtest/runner.py` does a **single disjoint split with no purge/embargo** at the boundary (bounded by intraday EOD-flatten, so any overlap is same-day).
3. `iter_market_states` `vol_delta` resets daily → first cycle each day has `vol_delta=0` for every strike (cold-start artifact).

---

## 11. Tests + full-suite result

- **New:** `tests/test_autoscalp_optionchain_wiring_audit.py` — 8 tests, all pass. Locks: (1) the 16-file AutoScalp decision path imports nothing from `app.optionchain`; (2) `app/optionchain/__init__` still declares research-only/no-order-path; (3) `decide_from_context` returns a safe non-BUY dict, no exception, on `None` / `[]` / all-None / `{"garbage":1}` / partial chains.
- **Existing coverage already proving the positive wiring** (not duplicated): `tests/test_sr_engine.py::test_oi_walls_contribute_in_index_mode` (chain OI → S/R zones + `oi_backing`), `tests/test_state_classifier.py` (`_oi_at` → `oi` component), `tests/test_autoscalp.py` (expiry-day chain provider, ZTH, cutoff, AUTO_ROLL), `tests/test_calibration_backtest.py::test_backtest_no_lookahead_calibration_is_frozen`, `tests/test_p61_filters_ablation.py`.
- **Full suite:** 794 passed (786 + 8 audit-lock), 0 failed. No production module touched.

---

## 12. Final GO / NO-GO

| | verdict |
|---|---|
| **A) Probability calibration** | **NO-GO** — leave production probability logic unchanged; keep accruing resolved LIVE outcomes toward a `regime\|signal_type` curve at `_MIN_ROWS=40`. Revisit when any cell clears 40 **and** a rolling walk-forward can be run with ≥ ~150 OOS trades. |
| **B) Option-chain AutoScalp wiring** | **NO-GO on new wiring** — the causal OI-wall + CE/PE-premium evidence is already wired and gating; PCR / max-pain / GEX / ΔOI-unwinding are not, and there is no OOS evidence that adding them improves the decision. Do not add them speculatively. |
| **C) Layer 4 ANN** | **NOT JUSTIFIED** — the constraint is data, not model form; the ANN backtest already returned INSUFFICIENT_SAMPLE. Stays DARK. |
| **D) Production** | **UNCHANGED** — no engine, config, calibration or execution change; additive test + docs only. |

### Known real defects surfaced (not fixed here — insufficient evidence / out of scope)

1. **Live calibration has no train/OOS split** — `_maybe_recalibrate` + `calibration_report` fit and grade the same unrestricted pool (`runner.py:1022`, `calibration_report.py:19-30`). This is the root methodological problem and is a **code fix**, but making it now (rolling-window fit + freeze) has no sample to validate against.
2. **State-classifier `oi` component is dead** — 12% of `state_score` weight, exactly 0.0 across all 99 live rows; root cause (chain `oi_chg` None/0 vs a `_oi_at` wiring bug) is OPEN.
3. **Live vs replay feature-regime mismatch** — degraded archive greeks + `chg_pct` asymmetry means offline calibration is fit on features that differ from live.
4. **Single-split, no-purge backtest** — `run_backtest` is not a rolling walk-forward.

These are logged for a future task with adequate data. **None warrants a production change today.**

---

_Production remains unchanged unless OOS evidence supports the change._
