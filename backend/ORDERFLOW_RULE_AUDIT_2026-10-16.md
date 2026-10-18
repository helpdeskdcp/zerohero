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

## 12. Real-data scoring replay (2026-10-17)

`scripts/orderflow_scoring_replay.py` (read-only, causal, policy thresholds as shipped, no tuning): 5m bars built from the real Dhan ticks (NATURALGAS 5 days, CRUDEOIL 5, NIFTY fut 3, BANKNIFTY fut 3), evidence from `scoring.py` adapters, entry next-bar open, exits +3/+6 bars, no forward-fill, 2 bps cost. Output: `data/research/orderflow/scoring_replay_2026-10-17.txt`.

- **New defect found by the replay and fixed (OF-16):** Dhan prices are float32 (`288.299987792969`, diffs 0.100006/0.099976), so the footprint adjacency check (exact tick diff) could never pass on real data, and the live engine never passed a tick size at all (`adjacency_verified` always false, imbalance evidence always `ADJACENCY_UNVERIFIED`). Fix: tick size now comes from the Dhan master (`SEM_TICK_SIZE`/100 — the field is in PAISE; verified NIFTY 0.1, BANKNIFTY 0.2, NATURALGAS 0.1, CRUDEOIL 1.0 against the observed price grid), plumbed through `instrument_meta` into the engine; footprint levels snap to the tick grid. Effect: imbalance evidence active on 1.2% of bars before the fix, 13.8% after.
- **Behaviour:** 1,632 scored bars -> 10 SIGNAL (0.6%), 1,526 WAIT, 96 NO_TRADE (OUTSIDE_SESSION). Main WAIT reasons: score below min, insufficient evidence groups, conflicting evidence, mandatory structure missing. Component availability: HTF 91%, VWAP 76%, structure 48%, sweep 37%, delta 23%, imbalance 14%, rvol 16%.
- **Result (descriptive only):** SIGNAL n=10 (9 NATURALGAS-heavy, ~5 days), forward result in signal direction H=3: mean -21.9 bps (hit 22%), H=6: -47.5 bps (hit 10%); a random-lead-bar null puts p(mean >= signal) at 0.98 / 1.00. One bar (-203/-244 bps) dominates the mean; excluding it the signals are still mostly negative. **No evidence of an edge; if anything the signals preceded reversals** (consistent with the negative inferred-delta relation in section 7). Effective sample is a handful of independent signals, so this is neither validation nor rejection of the idea.
- **Status:** scoring engine behaviour PASS (plumbing); predictive value NOT VALIDATED, thresholds uncalibrated. Do not wire to anything that trades.


## 13. Profit-oriented architecture (2026-10-17): measure first, trade only what passes

Principle: no strategy is assumed profitable. Every candidate must pass a pre-registered, net-of-cost, out-of-sample **promotion gate** (`app/strategy_lab/gate.py`, thresholds in `strategy_policy.json` -> `promotion`, fail-closed validator). The gate can only unlock PAPER; there is no promotion to live.

- **Gate criteria (all must pass):** n >= 200 trades over >= 40 distinct days; net mean >= 1 bps after cost; day-cluster bootstrap 95% CI lower bound > 0; >= 3 of 4 chronological folds positive and the LAST fold positive; still positive at 1.5x cost; top-5 trades <= 50% of profit and positive without them; drawdown bounded. `INSUFFICIENT_SAMPLE` is a legitimate answer.
- **Gate run on every available candidate** (`scripts/strategy_gate_run.py`, `data/research/strategy_lab/gate_report_*.txt`; costs: index 2 bps, MCX 3 bps; indices judged on 2024+ out-of-sample only): hybrid_mp v13 on NIFTY / BANKNIFTY / SENSEX / CRUDEOIL / NATURALGAS futures, smart_bos pine on NIFTY, overnight-long NIFTY / BANKNIFTY. **Result: PROMOTED = NONE.** Hybrid MP and smart_bos are net negative (CI entirely below 0 on NIFTY/BANKNIFTY). The overnight-long NIFTY drift is real in-sample (2017-26: +8.4 bps/night, CI +5.5..+11.2) but decayed out-of-sample (2024+: +2.5 bps, CI -1.8..+6.7, last fold negative) -> REJECT; the in-sample row is informational only and can never promote.
- **Trade lifecycle + risk manager** (`app/hybrid_orderflow/trade_engine.py`, 28 tests): premium-based option-buying plan (structural underlying stop translated through |delta|, max premium stop 35%, targets 1.5/2.5/4R, whole-lot partials, break-even after T1, trail to T1 after T2, 45 min time exit, structure invalidation), pessimistic fills (stop first when a bar touches stop and target, slippage on every exit, approximate statutory charges), risk budget that includes slippage and charges. Independent RiskManager: one position at a time (no pyramiding / averaging down), daily loss limit, max trades, post-loss cooldown, persistent kill switch; state is plain data so a restart cannot reset limits. Honest consequence: at paper capital Rs 2 lakh and 0.5% risk a NIFTY lot (75) cannot be sized (loss/lot ~ Rs 1,563 > Rs 1,000 budget) -> `RISK_BUDGET_TOO_SMALL`; the account size/risk setting decides whether the strategy is tradeable at all.
- **Shadow runner** (`app/hybrid_orderflow/shadow.py`, API `/api/hybrid_orderflow/status`, kill switch `HYBRID_ORDERFLOW_SHADOW=0`): reads the Dhan ticks read-only, runs the SAME `InstrumentEvaluator` as the replay (`bar_eval.py`, no look-ahead, tested), stores closed bars and scores in its own sqlite file, accumulates forward outcomes of SIGNAL bars, and feeds them to the gate. It rebuilds its session state from the persisted ticks after a restart (idempotent). At ~2 signals/day a gate verdict needs on the order of months; until then the answer is `INSUFFICIENT_SAMPLE` and no paper stage is unlocked. It never places or prepares an order.
- **Cleanup:** removed the dormant, defective `app/strategy/option_selection.py` (banker's rounding; superseded); dhan tick DB: 28 junk rows removed and 327k NULL-symbol rows backfilled from the current master (verified backup first, expired contracts left NULL); `ai_paper_trades` (duplicate MANUAL rows) and `live_market_snapshots` retention deliberately left untouched (financial history / heavy VACUUM under the trading lock) -- use `scripts/manual_pnl_reconcile.py` for the de-duplicated view. Other dormant modules (`orderflow/notify`, `platform/signals/contracts`, research CLIs) are kept: they have tests/docs and no runtime cost.


## 14. Edge scan with multiple-testing control (2026-10-18)

`app/strategy_lab/edge_scan.py` + `scripts/edge_scan_run.py` (8 unit tests; output `data/research/strategy_lab/edge_scan_2026-10-18.txt`). Pre-registered space of **534 mechanical hypotheses** on NIFTY 5m (2017-2026, 2,315 sessions): A slot-of-day drift x horizon x side (402), B opening-gap follow/fade (36), C first-30-minute move (24), D extreme 6-bar z-score follow/fade (36), E weekday effects (20), F prior-day return / close location (12), G last-hour move -> overnight (4). Nothing tuned. Net of 2 bps; test = one-sided cluster-by-day t-test.

Staging: S1 discovery on 2017-21 (Benjamini-Hochberg q=0.10) -> S2 validation on 2022-23 (BH q=0.05, only S1 survivors) -> S3 untouched holdout 2024+ (BH q=0.05, only S2 survivors) -> promotion gate. Placebo: 30 re-runs with each hypothesis' events replaced by a random same-size draw from its own opportunity pool.

**Result: 0 survivors at S1, S2 and S3.** The placebo lets through on average 1.17 false S1 survivors per run (max 3), the real data produced 0, i.e. no more signal than chance. Best raw TRAIN evidence: `G lasthour fade/follow overnight` (net +11..+12 bps, p 0.0013 / 0.007) which does not survive BH for 466 tests, and after that is inconsistent (OOS -5.7 for the fade, +5.3 p=0.17 for the follow); BANKNIFTY replication does not rescue it (OOS +2.1 p=0.35). Everything else is noise. Conclusion: on NIFTY at 5m resolution with price/time features only, no simple conditional edge clears 2 bps after correction. Any remaining candidate must be registered BEFORE seeing new data (forward test), not found by re-slicing this history.


## 15. Expiry-structure scan (NIFTY weekly short/long ATM straddle and strangle) and the bias that nearly fooled us (2026-10-18)

`app/strategy_lab/straddle_scan.py` + `scripts/straddle_scan_run.py` (10 tests). Data: Upstox expired-contract 5m bars, 60 NIFTY weekly expiries 2025-08-28..2026-10-13, 50 contracts per expiry (ATM +/-12 strikes around the PREVIOUS day's close), spot from our own series. 12 pre-registered hypotheses (short/long ATM straddle entered 09:20/10:30/12:00/13:30 and exited 15:15; short/long strangle +/-1 strike 09:20->15:15; short/long straddle held from 14:45 the day before to 09:30 on expiry day), explicit cost model (brokerage, STT on the sold premium, exchange/GST, slippage), Benjamini-Hochberg over the 12.

**First result (WRONG): 4 of 12 hypotheses survived (short strangle t=5.0, 81% wins).** An independent check on Angel One bid/ask quotes (histcap) showed large losses (-199, -104 pts) on days the Upstox set had silently excluded. Cause: the importer centred its strike window on the **expiry-day close** (look-ahead) -> on big-move days the 09:20 ATM fell outside the window -> those trades were dropped as "missing bars" -> exactly the worst days for a short-premium trade vanished (23 dropped trades, almost all |move| 100-160 bps). A second problem: the API rate limit (HTTP 429 UDAPI10005, ~2,000 calls per 30 min) had been counted as silent "errors".

**Fixes:** window centred on the previous close and widened to +/-12 strikes; 429 back-off with retry; `trade()` can carry a last traded price (counted, off by default) and the runner prints dropped/carried counts per hypothesis; index-spot lookups tolerate short outages; the original importer's look-ahead was corrected in place.

**Final result (n=60 expiries, nothing dropped): BH survivors = NONE.** Short straddle 09:20->15:15 +11.4 pts net (t=1.0, p=0.16, halves +29 / -7, worst -252 pts); short strangle +12.4 (t=1.2, worst -246); short straddle 10:30 +12.9 (t=1.5, p=0.07); overnight E-1 short straddle +11.6 (t=0.8, **worst -648 pts**, the day the first run had dropped). The long versions are negative. Angel bid/ask execution agrees with the Upstox bars where both exist (09-15: -199 vs -196; 10-13: -106 vs -116). Short premium into expiry collects a positive but statistically indistinguishable-from-zero average (+4..+5 bps) against a left tail of 20x the average win: **NO-GO** as a directionless strategy (consistent with the earlier iron-condor NO-GO). Lesson recorded: any "missing data -> no trade" rule must be audited against the outcome of the trades it drops.


## 16. MCX NATURALGAS / CRUDEOIL daily edge scan (2026-10-18)

`edge_scan.generate_daily` + `scripts/edge_scan_mcx_daily.py` (2 more tests; output `data/research/strategy_lab/edge_scan_mcx_daily_2026-10-18.txt`). Intraday MCX history is only ~3 months, so the scan is DAILY (1,215 sessions each, 2021-09..2026-09, Dhan continuous futures). Two data traps handled before any test: (1) daily bars carry UTC stamps of 18:30 -> converted to the IST session date (249 apparent weekend bars per instrument were a labelling artefact; 2 remain and are excluded); (2) contract rolls leave unadjusted overnight gaps (NATURALGAS gaps cluster on days 24-28 of the month) so ONLY gap-free quantities are used: signals from earlier sessions' open->close returns and ranges, outcome = next session open->close; no roll days are dropped (dropping by size would condition on the outcome).

92 pre-registered hypotheses (previous-session move follow/fade, cumulative 2/3/5/10-session move, close location, range expansion/compression, weekday), one BH family over both instruments, cost 3 bps, TRAIN 2021-09..2023-12 / VALID 2024-01..2025-06 / holdout 2025-07..2026-09. **Result: 0 survivors at all three stages** (placebo mean 0.1 false S1 survivors per run). Best TRAIN lead `NAT cum5d-down follow` (+43.6 bps, p=0.02) was +34 (p=0.06) in VALID and -12 in the holdout. Conclusion: no simple conditional edge on MCX natural gas / crude at daily resolution either.


## 17. Where Hybrid MP v13 fails, and the correction (2026-10-18)

`scripts/hybrid_mp_failure_analysis.py`, `scripts/hybrid_mp_cost_filter_test.py` (outputs in `data/research/strategy_lab/hybrid_*_2026-10-18.txt`).

**Diagnosis (corrected engine, NIFTY 5m, 6,188 trades).** Gross edge +0.89 bps/trade (TRAIN) and +0.91 (2024+) = +0.03 R, against a 2 bps round-trip cost. Mean stop distance is only 15-18 bps, so cost is 0.11-0.14 R, and for the third of trades with 7-8 bps stops it is 0.24-0.27 R. Gross by stop size: small stop -0.1..+0.04 bps, mid -0.07..+0.06, **large stop (24-31 bps) +2.7 bps** (TRAIN and 2024+ alike). Exit mix: 46% stop-outs (-14..-17 bps), 27% break-even, T4 only 9%. Entry type, side and hour carry no stable difference. **The strategy's premise** (continuation after a close-confirmed swing break) is right in sign but tiny in size: raw drift after a break is +0.7 bps (follow) / -0.7 (fade); 36 pre-registered break hypotheses, BH survivors 0. So the failure is magnitude: a continuation effect of ~1 bp cannot pay a 2 bp cost, and micro-stop trades (cost 0.2-0.3 R) are pure cost.

**Correction (pre-registered family min_risk_atr in {0.5, 1.0, 1.5, 2.0}, selection on TRAIN, VALID as confirmation).** NIFTY net bps/trade: TRAIN -1.1 / -0.7 / +0.2 / **+3.1** (p=0.008); VALID (2022-23) -1.4 / -1.2 / -0.2 / **+0.4** (p=0.38, n=392); 2024+ -1.1 / -0.8 / +0.3 / **+2.9** (p=0.03, NOT independent - its stop-size buckets were printed before the test). Same monotone dose-response with unchanged parameters on SENSEX (all-years net -1.0 -> **+1.75**, p=0.03) and BANKNIFTY (-1.7 -> +0.15, p=0.46). Implemented as `corrected_params()` (= fixed_params + min_risk_atr 2.0, max 3.0); the dashboard defaults to it (`variant=fixed` still available) and labels it NOT promoted.

**Honest status.** The mechanism (cost-dominated micro stops) is real and the dose-response replicates across three indices, but the resulting edge is small (+0.4..+3 bps net) and statistically weak where it is clean (VALID p=0.38, BANKNIFTY p=0.46); the promotion gate says PROMOTE only on the non-independent 2024+ slice. Therefore: research-only, NOT promoted. A frozen FORWARD record started on 2026-10-18 (`scripts/hybrid_mp_forward_report.py`, cron 16:40 weekdays, report `data/ops/hybrid_mp_forward_report.txt`): only trades after the freeze date count and the same gate judges them once n >= 200 (at ~1 trade per index per 1.4 sessions that is on the order of 4-6 months).
