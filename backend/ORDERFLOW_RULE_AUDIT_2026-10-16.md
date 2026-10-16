# Hybrid OrderFlow — Rule Audit, Policy, Corrections (2026-10-16)

Mode: **PAPER / SIGNAL-ONLY. Live order execution is DISABLED** (policy validator rejects `live_orders_enabled=true`; no order path was touched).
Nothing here is validated as profitable. Status vocabulary: PASS / FAIL / PARTIAL / NOT TESTED.

## 0. Discovery and baseline

- Repo `/root/zerohero`, branch `main`, backend `/root/zerohero/backend`, venv `backend/venv`, FastAPI app `app.main:app`, systemd `oi-dashboard.service` :7060 (paper, `live_trading=false`).
- Baseline before edits: full suite **3229 passed** (`./scripts/run_tests.sh -q`, 376 s). Final run after all edits: **3269 passed** (331 s).
- Active vs dormant (AST import graph, not file existence):

| module | importers | verdict |
|---|---|---|
| `app.orderflow.service/h1h7_state/profile/smart_money` | `orderflow.api` (main), `signals_dashboard`, `microstructure.evaluator` | ACTIVE (read-only / shadow) |
| `app.orderflow.depth` | `ai.shadow`, `reverse_engineering.*` | ACTIVE (informational) |
| `app.orderflow.premium_walk`, `orderflow.backtest` | scripts only | research |
| `app.orderflow.notify` | none | DORMANT |
| `app.engines.orderflow_engine` + `orderflow_store` + `connectors.dhan_ws/parser` | `app.main` (Dhan worker, 11 instruments, **LIVE now**: 1.55 M frames, auth VALID) | ACTIVE, observability only, no signals |
| `app.research_engines.orderflow.*` (v1 proxies) | `tri_compare.features`, backtests | research, FROZEN |
| `app.liquidity_sweep.*` | `strategy_mtf.*`, `index_signal_research` | research / verifier |
| `app.research_engines.hybrid_mp.smc` | `hybrid_mp.engine` | research, off by default |
| `app.engines.option_engine.select_option` | `scalp_strategy`, `smart_index_scalper.option_selector` | ACTIVE (autoscalp paper) |
| `app.strategy.option_selection.select_contract` | **none** | DORMANT (superseded) |
| `app.platform.signals.contracts.select_candidates` | **none** | DORMANT |
| `app.execution.killswitch` | order_manager, runtime, autoscalp safeguards, routes | ACTIVE |

## 1. Rule Audit Matrix

Data class: **G** genuine, **I** inferred (documented method), **P** OHLC/price proxy, **U** unavailable.

| ID | Rule (module → function) | Formula / threshold | Data | Finding | Decision |
|---|---|---|---|---|---|
| OF-01 | `dhan_parser.classify_tick` quote rule | ltp ≥ ask → BUY; ltp ≤ bid → SELL | I | **DEFECT**: no crossed/locked-book check; if bid ≥ ask both branches true and the first (BUY) won. Real data: 333 ticks on 2026-10-15, 27 on 10-16, 1 on 09-30 were labelled `QUOTE_RULE` with a crossed book | **FIX_IMPLEMENTATION** (done) |
| OF-02 | tick-rule fallback | ltp vs previous ltp; flat → UNKNOWN | I | formula correct, labelled `TICK_RULE*` | KEEP_VERIFIED |
| OF-03 | same-`ltt` de-dupe | drop packet when `ltt` unchanged | I | Dhan `ltt` has 1 s resolution: several real trades inside one second are collapsed to the last snapshot ⇒ delta/CVD **undercount**; unfixable without a trade-by-trade feed | **REQUIRES_DATA** (documented limitation) |
| OF-04 | `FootprintBar.delta` | buy_vol − sell_vol, UNKNOWN excluded | I | correct; had no `delta_pct` / classified volume | FIX_IMPLEMENTATION (added `delta_pct`, `classified_vol`) |
| OF-05 | `SymbolState.cvd` | running Σ signed ltq | I | **DEFECT**: never reset, carried across sessions/days | **FIX_IMPLEMENTATION** (reset on new IST day, `cvd_resets` exposed) |
| OF-06 | bar roll | `bar_start = ltt//bar_sec*bar_sec`, any other open bar closed | I | **DEFECT**: an out-of-order (older) tick closed the newest bar and reopened the old one | **FIX_IMPLEMENTATION** (late ticks counted, kept out of bars, still in CVD) |
| OF-07 | `stacked_imbalances` | ≥3 consecutive levels each ≥3:1 | I | **DEFECTS**: (a) "consecutive" meant next *occupied* price, not adjacent tick; (b) 1 lot vs 0 counted as imbalance (no min volume) | **FIX_IMPLEMENTATION** (min level volume 10 INITIAL; adjacency enforced when `tick_size` known, else flagged `adjacency_verified=false`) |
| OF-08 | tick persistence meta | `meta_provider().get(sid)` | — | **DEFECT**: meta dict keyed by *str* id, lookup by *int* ⇒ `symbol`/`kind` NULL in **all 538,393** persisted rows | **FIX_IMPLEMENTATION** (existing rows untouched) |
| OF-09 | CVD divergence | not implemented on genuine data; v1 `flow_vs_price` uses proxy CVD | P | proxy ≠ delta | DISABLE_UNSUPPORTED as a "delta" signal (stays research, named proxy) |
| OF-10 | absorption | `hcs.evidence` marks it UNOBSERVABLE | U | correctly never faked | REQUIRES_DATA |
| OF-11 | `depth.py` resting book | SnapQuote best-5 aggregates | G(resting) | labelled informational, not executed flow; earlier gate test REJECTED | KEEP_VERIFIED (informational only) |
| OF-12 | `profile.volume_profile` | `OHLCV_RANGE_DISTRIBUTION` | P | labelled approximation; index volume is 0 | KEEP (labelled) / REQUIRES_DATA for true POC/VAH/VAL |
| OF-13 | v1 `flow_proxy`, `cvd_proxy` | candle-colour pressure | P | not delta; policy forbids calling it delta | KEEP research-only, never a gate |
| OF-14 | `smart_money` volume-spike | vol ≥ 2× session avg | P | index candles have volume 0 ⇒ no-op on NIFTY/BANKNIFTY/SENSEX (see `VWAP_AUDIT.md`) | REQUIRES_DATA (use futures) |
| OF-15 | H1/H7 structural state | spike pctl ≥0.90, reclaim knee 0.60, H1_CONT gate | P (structure) | frozen research; 91 unit tests pass; **not independently re-derived in this audit** | NOT_TESTED vs policy (kept shadow; H7=AVOID only) |
| SMC-01 | `liquidity_sweep.detect_sweep` | wick beyond level + close back inside within 3 bars; backward scan only | P | wick without reclaim ≠ sweep; has a look-ahead mutation test; 108 liquidity_sweep tests pass | KEEP_VERIFIED |
| SMC-02 | hybrid_mp BOS/CHoCH | close beyond confirmed pivot | P | close-based, causal; strategy itself NO-GO (see `hybrid-mp-v13-nogo`) | KEEP (research) |
| SMC-03 | hybrid_mp `smc.py` filters | optional OB/FVG layer | P | no filter validated | DISABLE (off by default) |
| VW-01 | session VWAP (autoscalp) | Σ(tp·v)/Σv, None if Σv=0 | P | fail-safe None for indices; session/timezone reset **not re-tested here** | NOT_TESTED |
| OPT-01 | `strategy/option_selection.select_contract` | `round(spot/step)*step` ± offset | — | **DEFECT**: Python banker's rounding (`round(461.5)=462`) breaks ATM ties; no listed-strike/expiry/liquidity check; **dormant** | **REPLACE_RULE** → `hybrid_orderflow.option_select` (legacy left untouched, no importers) |
| OPT-02 | `option_engine.select_option` | quality score + ATM proximity (abs distance) | I | ACTIVE for paper autoscalp; proximity is symmetric (no ITM/OTM semantics); expiry validation lives upstream; **not changed** (NIFTY autoscalp frozen) | NOT_TESTED vs policy |
| OPT-03 | `platform/signals/contracts` | band ranker | — | dormant | REMOVE_DEAD_CODE (candidate, not removed) |
| RSK-01 | `execution.killswitch` | persistent kill switch | — | 8 tests pass | KEEP_VERIFIED |
| RSK-02 | risk manager independent of scoring | `autoscalp.safeguards` | — | not audited in depth | NOT_TESTED |

## 2. Incorrect / incomplete / duplicated / unsupported

Incorrect (fixed): OF-01, OF-05, OF-06, OF-07, OF-08. Incorrect (replaced, dormant): OPT-01. Unsupported (not fakeable): true aggressor flag, absorption, footprint on real tape, true volume profile, iceberg. Duplicated: three option selectors (OPT-01/02/03). Not audited deeply: RSK-02, VW-01, OPT-02, H1/H7 formulas.

## 3. Strategy policy

`app/hybrid_orderflow/strategy_policy.json` (v1.1.0, `DRAFT_UNVALIDATED`; v1.1.0 added scoring groups/caps/evidence rules) + `policy.py` validator. Fails closed (`PolicyError` ⇒ NO_TRADE/POLICY_INVALID) on: missing section, wrong major version, live enabled, mode not PAPER/SIGNAL_ONLY, candle finality off, forward-fill allowed, BOS on wick, score treated as probability, OTM ordered while disabled, averaging down/stop widening allowed, delta allowed from OHLC proxy, missing kill-switch/no-trade codes. All thresholds marked INITIAL/UNCALIBRATED are heuristics, not validated.

## 4. Architecture / integration map

```
Dhan WS (FULL, quote+depth) → dhan_parser.classify_tick [FIXED] → OrderflowEngine [FIXED] → footprint/CVD snapshot + TickStore
                                                                  (observability only; no signal, no order)
strategy_policy.json → policy.load_policy (fail closed) → option_select.select_contract (pure)   [NEW, NOT WIRED]
```
Nothing new is wired into autoscalp, the scalper, execution, or any signal producer; no feature flag was needed because there is no integration yet (so no duplicate signals/orders are possible). Rollback = `git revert` of the audit commits; no DB migration, no dependency added (policy is JSON to avoid a PyYAML requirement).

## 5. Selection examples (real output of `select_contract`)

- BULLISH, NIFTY 22997.35 → **CE 23000 ATM**, expiry 2026-10-20.
- BEARISH, 22997.35 → **PE 23000 ATM**.
- BEARISH with ATM illiquid → **PE 23050 ITM** (ATM rejected: `ILLIQUID`).
- Tie, spot 23025, strikes 23000/23050 → 23000 (explicit `LOWER_STRIKE` tie-break; `round()` would not be deterministic by policy).
- Irregular strikes `[22900,22950,23050,23100]`, spot 22997.35 → ATM 22950.
- OTM never selected by default; expired-only chain → `NO_TRADE: CONTRACT_EXPIRED`; crossed/locked, wide spread, stale quote, missing quote, delta out of band → `NO_TRADE` with reasons.

## 6. Data truth table

| data | status |
|---|---|
| Aggressor side / true Bid-Ask delta | **UNAVAILABLE** from every broker checked; only quote-rule/tick-rule INFERENCE exists (Dhan FULL packets) |
| Dhan inferred delta/CVD/footprint | INFERRED, live, 1 s `ltt` resolution (undercount), captured **6 sessions**: 2026-09-29/30, 10-01, 10-15, 10-16 (+ 28 junk rows with 1970/1980 `ltt`, test pollution, not deleted) |
| Bid/ask depth (best-5) | GENUINE resting book (not executed trades), `l2_capture.db` short history |
| Volume profile | OHLCV approximation; indices have volume 0 |
| Absorption / iceberg | UNAVAILABLE |
| Greeks for option selection | available from chain (Angel/Dhan), not wired here |
| Historical option quotes with spread | 11 sessions only (Aug 2026), see hybrid-mp work |

## 7. Backtest / validation (where valid data exists)

`scripts/orderflow_delta_predictiveness.py` (read-only; crossed-book rows excluded). Inferred delta_pct per 1-minute bar vs next-5-minute return, real ticks:

| instrument (sec id) | days | bars | Spearman | hit-rate when \|delta_pct\| ≥ 0.3 |
|---|---|---|---|---|
| NATGAS fut 570750 | 5 | 3,234 | −0.082 | 0.395 |
| CRUDEOIL fut 569900 | 5 | 3,135 | −0.046 | 0.469 |
| NIFTY fut 48704 | 4 | 1,163 | −0.063 | 0.440 |
| BANKNIFTY fut 48699 | 4 | 1,147 | −0.091 | 0.442 |
| SENSEX fut 864571 | 5 | 910 | −0.100 | 0.439 |

All five liquid instruments show a small **negative** relation (not positive). Overlapping bars ⇒ effective n far below "bars"; quote-rule delta also correlates with bid-ask bounce (mean-reversion artefact). **No predictive content demonstrated; INSUFFICIENT_SAMPLE for any go/no-go.** Output: `data/research/orderflow/delta_predictiveness_2026-10-16.txt`. Prior evidence unchanged: Hybrid MP v13 NO-GO, tick-rule CVD NO-GO, L2 imbalance gate REJECTED, H1/H7 on premium NOT VALIDATED.

## 8. Files

Created: `app/hybrid_orderflow/{__init__,policy,option_select}.py`, `strategy_policy.json`, `tests/test_hybrid_orderflow_policy.py`, `tests/test_orderflow_policy_fixes.py`, `scripts/orderflow_delta_predictiveness.py`, this report. Modified: `app/connectors/dhan_parser.py` (OF-01), `app/engines/orderflow_engine.py` (OF-04…08).

## 9. Tests

- New: 31 (policy/selector) + 9 (OrderFlow regressions) = 40 tests; the first 7 regression tests were verified to **fail on the old code** (fix stashed), the 2 added later (delta_pct, meta key) were written with their fix.
- Related groups: dhan_connector + orderflow_store 57 passed; h1h7/profile/depth/smart_money 91 passed; liquidity_sweep 108 passed; killswitch 8 passed.
- Full suite after all edits: **3269 passed, 0 failed** (baseline 3229 + 40 new).

## 10. Not done / blockers

- **Deployment:** the live Dhan worker still runs the OLD classifier/engine in memory; fixes take effect only after a service restart (needs the owner's go; not done).
- **Scoring engine:** built 2026-10-16 (`app/hybrid_orderflow/scoring.py`, 29 unit tests, paper-only, unwired). Thresholds INITIAL/uncalibrated; NOT run on real data yet; the score is a count of confirmed evidence, not a probability.
- **Not implemented (honestly):** entry/exit/stop/target engine, risk manager integration, paper-trading workflow for this engine, provider-fallback double-count guard, SMC non-repainting re-implementation, session-VWAP timezone re-verification. These are `NOT TESTED`/not built; the policy and selector are the foundation only.
- **Data blockers:** genuine aggressor/tick tape, true volume-at-price, per-stock derivatives data (equity derivatives disabled in policy), more than 6 sessions of inferred ticks.
- Existing 361 crossed-book rows and 538 k NULL-symbol rows are left as-is (no destructive migration).

## 11. Validation status

| item | status |
|---|---|
| Policy load/validation, fail-closed | PASS (31 tests incl. selector) |
| Corrected OrderFlow rules OF-01/05/06/07/08 | PASS (unit) — not deployed |
| CE/PE + ITM/ATM/OTM selector | PASS (unit) — not integrated |
| Genuine delta / CVD / footprint / absorption | PARTIAL (inferred only) / NOT TESTED on live |
| Scoring engine (unit) | PASS (29 tests: caps, conflict, mandatory gates, fail-closed gates, adapters) — not wired, not run on real data, not calibrated |
| Entry/exit, risk integration | NOT TESTED (not built) |
| Backtest with costs | NOT TESTED (no valid genuine data; inferred study is descriptive) |
| Production readiness | **FAIL / NOT READY** |
| Live order execution | **DISABLED** (confirmed: policy + no wiring + killswitch tests) |
