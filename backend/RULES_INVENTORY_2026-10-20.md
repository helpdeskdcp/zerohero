# ZeroHero rules inventory

registry 2026-10-20 · 34 rules · drift problems: **0** · unclassified env flags: **0**

- by mode: {'ENFORCED': 21, 'OFF_BY_DEFAULT': 3, 'SHADOW': 7, 'ADVISORY': 1, 'OFFLINE': 1, 'DARK': 1}
- by evidence: {'SAFETY_RAIL': 14, 'UNVALIDATED': 17, 'DERIVED_FROM_LIVE_OUTCOMES': 1, 'NEGATIVE': 1, 'DRAFT': 1}
- by layer: {'L0_execution': 6, 'L1_runner_safeguards': 8, 'L2_strategy': 7, 'L3_signal_gate': 1, 'L4_hcs_shadow': 6, 'L5_optionchain': 1, 'L6_hybrid_orderflow': 2, 'L7_offline_gates': 1, 'L8_dark': 2}

## 1. Enforced on the default paper path but NOT validated as alpha (evidence != safety rail)

These rules change which trades are taken; none has independent out-of-sample, net-of-cost evidence. Safety rails are excluded.

| rule | layer | mode | evidence | note |
|---|---|---|---|---|
| SG-MAX-TRADES | L1_runner_safeguards | ENFORCED | UNVALIDATED |  |
| SG-CONSEC-LOSSES | L1_runner_safeguards | ENFORCED | UNVALIDATED |  |
| SG-DAILY-LOSS | L1_runner_safeguards | ENFORCED | UNVALIDATED |  |
| SG-OPTION-LIQUIDITY | L1_runner_safeguards | ENFORCED | UNVALIDATED |  |
| ST-FILTER-BLOCKS | L2_strategy | ENFORCED | DERIVED_FROM_LIVE_OUTCOMES | Derived from pooled live paper outcomes (n=167, 2026-08-31..09-29) with a chronological split; the 'test half' |
| ST-CONFIDENCE-MAP | L2_strategy | ENFORCED | UNVALIDATED |  |
| ST-EV-GATE | L2_strategy | ENFORCED | UNVALIDATED | EV uses the calibrated win probability; calibration overconfidence (K8) fixed 2026-09-21, post-fix live n=13. |
| ST-RISK-ENGINE | L2_strategy | ENFORCED | UNVALIDATED |  |

## 2. Parameter conflicts (same concept, different values)

| concept | severity | values (rule=value unit mode) |
|---|---|---|
| daily_loss_cap | REVIEW_UNITS | SG-DAILY-LOSS=3000.0 INR [ENFORCED]; ST-RISK-ENGINE=3 pct_of_capital [ENFORCED]; HOF-POLICY-RISK=2.0 pct_of_capital [SHADOW] |
| max_consecutive_losses | ENFORCED_CONFLICT | SG-CONSEC-LOSSES=4 [ENFORCED]; ST-RISK-ENGINE=3 [ENFORCED] |
| max_feed_age_sec | ENFORCED_CONFLICT | EXEC-STALENESS=20.0 [ENFORCED]; SG-FEED-FRESH=12 [ENFORCED] |
| max_trades_per_day | ENFORCED_CONFLICT | SG-MAX-TRADES=18 [ENFORCED]; ST-RISK-ENGINE=10 [ENFORCED]; HOF-POLICY-RISK=6 [SHADOW] |
| min_ev_r | INFO | ST-EV-GATE=0.12 [ENFORCED]; HCS-VETO-POOR-EV=0.05 [SHADOW] |
| min_reward_risk | INFO | ST-EV-GATE=1.3 [ENFORCED]; SIG-FINAL-GATE=1.5 [OFF_BY_DEFAULT]; HCS-VETO-RR=1.3 [SHADOW]; HOF-POLICY-RISK=1.3 [SHADOW] |

## 3. Shadow / dark / offline (computed or present, but not blocking trades)

- **HCS-VETO-LIQUIDITY** [SHADOW] HCS veto - option spread — 
- **HCS-VETO-RR** [SHADOW] HCS veto - poor reward/risk — 
- **HCS-VETO-DATA-QUALITY** [SHADOW] HCS veto - data quality — 
- **HCS-VETO-NEARBY-SR** [SHADOW] HCS veto - opposing S/R too close — 
- **HCS-VETO-POOR-EV** [SHADOW] HCS veto - poor EV — 
- **HCS-VETO-OTHER** [SHADOW] HCS vetoes: exhaustion, fake_breakout, mtf_conflict, noisy_calibration — HCS calibration NOT VALIDATED over 6 sessions (memory hcs-engine).
- **OC-QUALIFY** [ADVISORY] Option-chain gates G_DQ/G_STRUCTURE/G_DIRECTION/G_ANN/G_LOCATION/G_GEX/G_EXPIRY -> QUALIFIED|WATCH|NO_TRADE — Display/advisory; wiring into entries was tested and rejected (ST-CHAIN-GATE).
- **HOF-POLICY-RISK** [SHADOW] Hybrid order-flow policy risk limits (shadow runner) — policy_version 1.2.0 validation_status DRAFT_UNVALIDATED; live orders can never be enabled by this policy (validator fails closed).
- **PROMO-GATE** [OFFLINE] Promotion gate for any new strategy (net-of-cost, folds, stress, concentration) — Result so far: PROMOTED = NONE (Hybrid MP, SmartBoS, PA, expiry pin all fail).
- **SB-ADAPTATION** [DARK] Structural-break adaptation (can halt/tighten safeguards) — No non-test caller of apply_to_safeguards: not wired into the runner.

## 4. Off by default (code exists, switch off)

- **ST-SR-GATE** Live support/resistance confirmation gate — switch `sr_gates.enabled` — evidence UNVALIDATED
- **ST-CHAIN-GATE** Option-chain PCR/OI confirmation gate — switch `chain_gates.enabled` — evidence NEGATIVE
- **SIG-FINAL-GATE** High-confidence single-signal gate (score, RR, duplicate, cooldown, late-entry) — switch `final_signal_gate.enabled (+ shadow_mode, default True when enabled)` — evidence UNVALIDATED

## 5. Safety rails (enforced, tested, no alpha claim)

- EXEC-LIVE-GATE: Live order triple gate
- EXEC-KILLSWITCH: Global kill switch
- EXEC-ENTRIES-BLOCKED: Order manager entry block (kill / risk halt / frozen)
- EXEC-STALENESS: Stale market-data / reconcile guard for NEW positions
- EXEC-RATELIMIT-BREAKER: Broker rate limit + circuit breaker
- EXEC-IDEMPOTENCY: Idempotent order tags (no double submit)
- SG-SESSION-WINDOW: Session window (NSE 09:20-15:00, MCX to 23:00)
- SG-FEED-FRESH: Market-data feed connected and fresh
- SG-MAX-CONCURRENT: Max concurrent positions + no duplicate underlying/side
- SG-CALENDAR: Weekend / exchange-holiday block
- ST-NO-TRADE-CLASSIFIER: No-trade reason classifier (data / conflict / market-closed)
- HOF-POLICY-EXECUTION: Policy execution mode is PAPER
- AI-BREAKER: AI-provider circuit breaker (AI layer degrades, rules keep running)

## 6. Coverage gaps (NOT inventoried yet)

- `app/engines/scalp_strategy.py` — decide_from_context internals beyond the filter defaults (score blending, MTF/regime weights, option selection) are not enumerated rule-by-rule
- `app/engines/state_classifier.py` — signal-state classification rules
- `app/engines/option_engine.py` — select_option / analyse_leg quality rules
- `app/nse_official/controls.py` — production_gate
- `app/hedging/runner.py` — _kill_switch_breach
- `app/platform/marketdata/service.py` — trading_blocked
- `app/mathematical_confluence` — research-only confluence engine
- `app/institutional_edge` — edge-score state machine (research)
- `app/liquidity_sweep` — NOT READY strategy (research)

## 7. Environment flags (read under app/)

| name | category | rule | default(s) | first use |
|---|---|---|---|---|
| AI_BREAKER_FORCE | SAFETY_SWITCH | AI-BREAKER | [None] | app/ai/ai_client.py:45 |
| CHANAKYA_ALLOW_LIVE | SAFETY_SWITCH | EXEC-LIVE-GATE | [None] | app/scalper.py:243 |
| CHANAKYA_LIVE_CONFIRM_TOKEN | SAFETY_SWITCH | EXEC-LIVE-GATE | [None] | app/execution/angelone_broker.py:55 |
| CHANAKYA_RENKO_SHADOW | FEATURE_FLAG |  | ['1'] | app/autoscalp/renko_shadow.py:56 |
| DHAN_ORDERFLOW_AUTO_FUTURES | FEATURE_FLAG |  | [None] | app/connectors/dhan_ws.py:78 |
| HYBRID_MP_ENABLED | FEATURE_FLAG |  | ['1'] | app/main.py:307 |
| HYBRID_ORDERFLOW_SHADOW | FEATURE_FLAG | HOF-POLICY-RISK | ['1'] | app/hybrid_orderflow/shadow.py:31 |
| PA_OF_LAB | FEATURE_FLAG |  | ['1'] | app/main.py:317 |
| PLATFORM_STREAM | FEATURE_FLAG |  | ['1'] | app/platform/live.py:40 |
| RULES_REGISTRY | FEATURE_FLAG |  | ['1'] | app/main.py:325 |
| SMART_SCALPER_AUTO_ARM | FEATURE_FLAG |  | [None] | app/smart_index_scalper/scheduler.py:155 |

(+ 67 secret/path/tuning/provider variables, classified by pattern)

## 8. Drift problems

none