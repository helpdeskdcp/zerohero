# MATHEMATICAL_CONFLUENCE_ENGINE_V1

**Status:** RESEARCH / PAPER-ANALYSIS ONLY · `live_trading=false` · no order path.
**Calibration:** UNCALIBRATED — the 7-sub-score weights are configurable defaults,
not fitted. No backtest yet (spec §26). Do NOT treat the score as a probability.

An evidence-based mathematical + OI confluence engine. It never signals on a
single indicator — every output is a weighted composite of 7 sub-scores, each
with its own reason codes.

---

## 1. Reuse vs. new (spec §8, §35 — inspected first)

| already in the codebase — REUSED | new in this engine |
|---|---|
| `engines/turning_point_engine._pivots` — classical P/R1–3/S1–3 (§2) | Gann balance / range levels, labelled "GANN MATHEMATICAL LEVEL" (§3) |
| `expiry_zero_to_hero.oi_change.classify_oi_action` — the CE/PE OI+LTP matrix (§6) | explicit level → confluence-zone table with `evidence_count` (§5, §21) |
| `engines/sr_engine` prev-day levels, `_gex_profile` OI walls, swing/confluence | causal swing detector with strength/age/touch/rejection (§4, §27) |
| `engines/paper_trading` open/update/close + trailing (§19) | market-position ratios + regime classifier (§12) |
| `autoscalp/safeguards` — max_per_day / daily_loss / cooldown (§18) | 7 sub-scores + weighted `CONFLUENCE_SCORE`, configurable (§13) |
| `autoscalp/data_quality` — AVAILABLE/MISSING/UNSUPPORTED (§28) | multiplicative OI-wall score + top-3 + BATTLE_ZONE (§7, §22) |
| `db._MIGRATIONS` — additive schema (§30) | the NATURALGAS pivot/Gann validation (§25, unit-tested) |

---

## 2. Module map — `app/mathematical_confluence/`

| file | role | spec |
|---|---|---|
| `levels.py` | `classical_pivots` (reuses `_pivots`), `gann_levels`, `normalized_levels` — flattens every math/structure/OI level to `{value, family, source, weight}` | §2, §3 |
| `confluence.py` | `cluster_levels` — merges levels within tolerance into zones with `center / zone_low / zone_high / evidence_count / families / strength_score / side`; `high_confluence_zones` (≥3 independent families) | §5, §8, §21 |
| `market_position.py` | `market_position` (position-in-prev-range, position-in-day-range, open-vs-PDC/PDH/PDL, gap type), `classify_regime` → one of 7 regimes + reasons | §12 |
| `swings.py` | `detect_swings` — a pivot needs `n` bars each side, so it is confirmed only at `index+n`; `now_index` caps what the caller may know → **no look-ahead**; `swing_stats` touch/rejection/age/strength | §4, §27 |
| `oi_confluence.py` | `oi_matrix` — per-strike CE/PE OI %, LTP %, interpretation + **confidence** (not absolute), wall scores, support/resistance/battle scores, `CALL_RESISTANCE_WALL`/`PUT_SUPPORT_WALL`/top-3/`BATTLE_ZONE`, PCR | §6, §7, §22 |
| `scoring.py` | the 7 sub-scores + `confluence_score(sub, weights)` → 0–100 with a per-sub breakdown; weights `CONFIGURABLE_DEFAULT (NOT calibrated)` | §13 |
| `engine.py` | `MathematicalConfluenceEngine.evaluate(...)` — the orchestrator: data-quality gate → levels → zones → market position/regime → direction hypothesis → 7 sub-scores → `signal_type` + gates → structural SL/T1/T2/T3 + RR | §14, §20, §28, §33 |
| `api.py` | `GET /api/mathematics/{levels,confluence,oi,market-map,signal}` — read-only, pulls live context from `market_data.selection_snapshot` + daily/5m candles | §29 |

---

## 3. Formulas implemented

**Classical floor pivots (§2)** — via `turning_point_engine._pivots`, one source of truth:
```
P  = (PDH + PDL + PDC) / 3
R1 = 2P − PDL      S1 = 2P − PDH
R2 = P + (PDH−PDL) S2 = P − (PDH−PDL)
R3 = PDH + 2(P−PDL) S3 = PDL − 2(PDH−P)
```

**Gann mathematical levels (§3)** — NOT guaranteed targets:
```
GANN_BALANCE = (PDH + PDL) / 2
RANGE        = PDH − PDL
GANN_UP_k    = BALANCE + RANGE·k/4   (k = 1..4)
GANN_DOWN_k  = BALANCE − RANGE·k/4
```

**Market position (§12):**
```
position_in_prev_day_range = (P − PDL) / (PDH − PDL)
position_in_intraday_range = (P − DayLow) / (DayHigh − DayLow)
open_vs_prev_close/high/low = TodayOpen − {PDC, PDH, PDL}
```

**CONFLUENCE_SCORE (§13):** `Σ (subscore/subscore_max) · weight · 100`, defaults
mathematical 20% · oi 20% · price_action 20% · volume 10% · breakout 10% ·
retest 10% · swing 10%.

**NATURALGAS validation (§25) — computed dynamically, unit-tested:**
`PDH 282.20 / PDL 277.20 / PDC 278.80` →
`P = 279.40`, `Gann balance = 279.70`, `RANGE = 5.00`,
`R1 281.60 · R2 284.40 · R3 286.60 · S1 276.60 · S2 274.40 · S3 271.60`.
The clusterer merges `pivot 279.40 + gann_balance 279.70 + OI wall 280.0` into
one support confluence zone (`evidence_count ≥ 3`); `277.20` stays a distinct
PDL support zone. No level is hard-coded.

---

## 4. Engine flow (§34)

```
inputs (prev_day OHLC, today_open, price, day H/L, 1/3/5/15m bars, option chain)
  │
  ├─ DATA-QUALITY GATE ── missing PDH/PDL/PDC/price → DATA_INSUFFICIENT + exact fields (§28)
  │
  ├─ SWING ENGINE (causal, no look-ahead) ─────────── swing_high/low, strength, touches
  ├─ LEVEL MATH ──────── pivots + Gann + prev-day + today-open + swings + OI walls
  ├─ CONFLUENCE ─────── cluster within tol → zones (evidence_count, strength)
  ├─ OI MATRIX ─────── per-strike interpretation + confidence, walls, battle zone, PCR
  ├─ MARKET POSITION + REGIME ─── 7 regimes, rule-based, with reasons
  ├─ DIRECTION HYPOTHESIS ─── from regime + zone geometry + momentum (never 1 signal)
  ├─ 7 SUB-SCORES ─── mathematical / oi / price_action / volume / breakout / retest / swing
  ├─ CONFLUENCE_SCORE ─── weighted composite 0–100 + breakdown
  ├─ SIGNAL TYPE + GATES ─── BUY_CE / BUY_PE / WAIT / *_WATCH / NO_TRADE (+ no_trade_reason)
  └─ STRUCTURAL PLAN ─── entry_zone, SL (swing/zone invalidation), T1/T2/T3 (next zones), RR
```

Signal output carries: `instrument, timestamp, spot, direction, signal_type,
confidence, confluence_score, score_breakdown, market_regime, market_position,
nearest_support/resistance, confluence_zones, high_confluence_zones, oi_matrix,
entry_zone, stop_loss, target_1/2/3, risk_reward, mathematical_levels,
{oi,price_action,volume,swing,mathematical,breakout,retest}_evidence,
reason_codes, no_trade_reason, calibration`.

`NO_TRADE` is a first-class output with an explanation (§33): conflicting OI,
thin evidence (<2 families on the target zone), volume below average, score
below threshold, or `DATA_INSUFFICIENT`.

---

## 5. Tests

`tests/test_mathematical_confluence.py` — **9 pass**. Full suite **406 pass**.
Covers: NATURALGAS pivot + Gann validation (§25); confluence zone forms around
279.4–280 from dynamic levels; **anti-look-ahead** swing confirmation (§27);
market position + regime (§12); OI matrix interpretation + walls with
confidence not absolutes (§6/§7); `DATA_INSUFFICIENT` with exact missing fields
(§28); full evaluation shape + 7-sub-score composite (§13/§14); configurable
weights (§13).

Endpoints verified live (200): `/api/mathematics/levels|confluence|oi|market-map|signal`
returning real NIFTY pivots (P 23871.9) + Gann (balance 23850.6) from live
prev-day OHLC.

---

## 6. NOT built yet — the remaining spec, in priority order

The math core + engine + API + validation are done. These are follow-on slices:

1. **SMART_INDEX_SCALPER** (§16, §17, §34) — orchestration layer reusing
   `autoscalp` universe/liquidity/ranking + this engine per index, `INDEX_SELECTION_SCORE`,
   rank #1/#2/#3 with an explanation.
2. **Profile-based paper trading** (§18, §19) — CONSERVATIVE/BALANCED/AGGRESSIVE
   thresholds over `autoscalp.safeguards` + `paper_trading` state machine.
   **Reuse, do not fork, the existing risk controls.**
3. **Option selection** (§15) — reuse `engines/option_engine.select_option`; feed
   this engine's direction + target zone.
4. **DB tables** (§30) — `mathematical_levels`, `confluence_zones`, `oi_snapshots`,
   `smart_scalper_signals`, `paper_trades` via `db._MIGRATIONS`. Store full evidence.
5. **Backtest / replay** (§26, §27) — historical replay per timestamp; metrics per
   profile and per index; strict causal (reuse the Expiry-Zero-to-Hero anti-lookahead
   discipline). **No profitability claim until this runs.**
6. **Frontend** (§21–24, §31, §32) — "ADVANCED MATHEMATICAL SCALPER" section:
   Mathematical Market Map table, OI Confluence Matrix, Smart Index Ranking,
   Paper Trade Signal card, "WHY THIS TRADE?" breakdown, Live Market Map ladder.
   Never "BUY CE 90%" alone — always the sub-score breakdown + reasons + risks.

---

## 7. Known limitations

- **UNCALIBRATED** — score weights and thresholds are hand-set; the mapping from
  `confluence_score` to win-rate is unknown until §26 runs.
- Breakout / retest / reversal / candle-signal detection are **caller-supplied
  inputs** to `evaluate()` right now (the engine consumes `breakout_state`,
  `retest_state`, `candle_signals`, `reversal_candidate`). A dedicated price-action
  sub-engine that derives these from bars is part of the SMART_INDEX_SCALPER slice.
- `/api/mathematics/*` fetch live candles + chain per call (4–8 s) — fine for
  research, needs a short cache before dashboard polling.
- OI `oi_change` is only as good as the source: the live chain path provides
  absolute OI; ΔOI is derived by the caller (see `expiry_zero_to_hero.oi_change`).

---

## 8. SLICE 2 — SMART_INDEX_SCALPER (built)

`app/smart_index_scalper/` — the orchestration layer (spec sections 16, 17, 34).
RESEARCH ONLY. **Does NOT open a paper position** (that is slice 3).

| file | role |
|---|---|
| `universe.py` | configurable index list (`SMART_SCALPER_UNIVERSE` env, default NIFTY/BANKNIFTY/FINNIFTY/SENSEX/MIDCPNIFTY); `index_meta` reuses `autoscalp._sym_meta` |
| `eligibility.py` | the 8 section-16 filters — valid_option_chain, liquidity (ATM OI), reasonable_spread, sufficient_volume, clear_mathematical_levels, clear_oi_structure, acceptable_confidence, acceptable_risk_reward. Each returns (passed, reason); missing data = FAIL, never silent pass |
| `selection_score.py` | `INDEX_SELECTION_SCORE` (section 17): 25% signal quality / 20% OI confluence / 15% math confluence / 15% liquidity / 10% volume / 10% momentum / 5% RR. Configurable, UNCALIBRATED. `explain_winner()` = why #1 beat #2 |
| `scanner.py` | `SmartIndexScalper.scan(symbols)` — per index: shared `market_context` (20 s TTL cache) → `MathematicalConfluenceEngine.evaluate()` → `oi_matrix` → eligibility → component scores → rank eligible, pick #1/#2/#3, explain |
| `api.py` | `GET /api/smart-scalper/{ranking,signal}` — read-only |

Shared: `mathematical_confluence/context.py` (`market_context`) — extracted so
both engines fetch live prev-day OHLC + 5m bars + chain once, cached.

Verified live (markets closed, ~21:00 IST): `/api/smart-scalper/ranking` → 200,
`ranked: []`, every index correctly **not_eligible** on `acceptable_confidence`
+ `acceptable_risk_reward` with the failed-filter list shown, `why #1 = "no
eligible index"`. The scalper declines rather than forcing a pick (section 33).

Tests +6 (`test_smart_index_scalper.py`): configurable universe, all-8 filters
pass/fail with named reasons, weighted+configurable selection score, full scan
ranks NIFTY > BANKNIFTY and marks a data-missing SENSEX not-eligible, and a
static check that the package contains no `open_trade`/`place_order`/`OrderManager`.
Full suite 412 pass.

Remaining slices: 3 option selection (reuse `option_engine.select_option`) ·
4 DB tables · 5 backtest/replay (required before any profitability claim) ·
6 frontend.

---

## 9. SLICE 3 — SMART CE/PE OPTION SELECTION (built)

`app/smart_index_scalper/{option_selector,profiles}.py` (spec section 24, old §15).
RESEARCH ONLY. Reuses `engines.option_engine.analyse_leg` + `select_option`
(spec §47/§52 — no duplicate scoring).

**`option_selector.select(direction, spot, chain, atm, strike_step,
expected_move_pts, allowed_option_distance, premium_min/max, weights)`** —
- candidate strikes = wanted side, within `allowed_option_distance` strikes of ATM,
  premium in `[min, max]`;
- `analyse_leg({}, leg, opt_type, index_move_pts=expected_move, light=True)` per
  candidate → quality / translation / delta_fit / theta_drag / iv_context / liquidity;
- deterministic `selection_score` (0–100) = leg_quality 30 · liquidity 20 ·
  translation 15 · premium_momentum 10 · atm_distance 10 · spread 10 · theta 5
  (configurable, UNCALIBRATED);
- returns `selected_strike / option_type / option_ltp / delta / oi / oi_change /
  volume / spread / iv / iv_context / selection_score / candidates[] / reasons[] /
  expected_index_move_pts`. Same inputs → same pick.

**`profiles.get_profile(name)`** — CONSERVATIVE / BALANCED / AGGRESSIVE config
dicts (§25): `min_confidence`, `min_selection_score`, `min_rr1`,
`max_trades_per_day`, `risk_per_trade_pct`, `max_daily_loss`, `cooldown_sec`,
`allowed_option_distance` (1 / 2 / 3), SL/target ATR mults, `required_confirmations`.
Env-overridable. UNCALIBRATED defaults; **paper entry still routes through
`autoscalp.safeguards` on top of these** (slice 4).

`scanner.scan()` now attaches `selected_option` to any eligible directional
(BUY_CE/BUY_PE) index result, using the profile's `allowed_option_distance` and
`|target_1 − spot|` as the expected move. New endpoints:
`GET /api/smart-scalper/{option, profiles}`; `/ranking` and `/signal` take a
`profile=` param.

`mathematical_confluence/context.py` chain rows now carry per-leg broker greeks
(Δ/Γ/Θ/V/IV + `greeks_source`) so the selector's premium-move estimate is real
for NIFTY/BANKNIFTY (MCX/SENSEX stay `greeks_source=UNAVAILABLE`, selector
degrades to OI+premium+ATM-distance and says so).

Verified live: `/api/smart-scalper/option?symbol=NIFTY&direction=CE` → 23900 CE
@ ₹102.45, selection_score 76.4, delta 0.497, expected premium move ≈ ₹26.7 on
a ~60-pt index move, 5 ranked candidates + reasons.

Tests +5 (`test_smart_index_scalper.py`): configurable profiles with ATM-distance
band; deterministic + explainable selection; profile band respected;
NO_SELECTION / DATA_INSUFFICIENT gates; static no-order-path check. Full suite
417 pass.

Remaining: slice 4 (DB tables + profile-driven paper-trade state machine over
`paper_trading` + `safeguards`) · slice 5 (historical replay/backtest — required
before any profitability claim) · slice 6 (frontend).

---

## 10. SLICE 4 — DB TABLES + PAPER-TRADE STATE MACHINE (built)

RESEARCH / PAPER ONLY. `live_trading` stays false, NO live-order path (static-tested).
Reuses `engines.paper_trading.{open_trade,update_trade_price,close_trade}` (the
same paper engine the autoscalp runner uses) and `autoscalp.safeguards.Safeguards.
check_entry` — **risk controls are never bypassed**.

### DB (via `db._MIGRATIONS`, additive, no data loss)
- **`smart_scalper_signals`** (PK signal_id, write-once) — full signal + evidence
  audit: spot/direction/signal_type/confidence/confluence_score/index_selection_score/
  regime, entry_zone/SL/T1-3/RR, selected_strike/option_ltp/selection_score,
  nearest S/R, reason_codes, no_trade_reason, eligibility_json, evidence_json,
  invalidation, terminal `state`, `trade_id`.
- **`smart_scalper_states`** — every transition: from/to state, action, reason,
  spot, option_mark, pnl, mfe, mae.
- Positions themselves = `ai_paper_trades WHERE strategy='SMART_SCALPER'` (reuse).
- `db` helpers: `insert/update/list_smart_scalper_signal(s)`, `log/list_smart_scalper_states`.

### State machine — `state_machine.py` (spec §19 & §26)
`NO_TRADE / WATCHING / SETUP_FORMING / ENTRY_READY / ENTRY_CONFIRMED / PAPER_OPEN /
TARGET_RUNNING / EXIT_WARNING / EXITED / STOPPED / INVALIDATED`.
- `pre_entry_state(scan_row, profile)` — `ENTRY_CONFIRMED` only when EVERY gate
  passes: eligible + directional BUY_CE/BUY_PE + confidence >= profile.min_confidence
  + index_selection_score >= profile.min_selection_score + RR1 >= profile.min_rr1
  + option selection OK + all `required_confirmations` present in reason_codes
  (level / oi / volume / price_action). One confirmation short -> `ENTRY_READY`.
  Conflicts / not eligible / NO_TRADE signal -> `NO_TRADE` with the reason.
- `in_trade_state(position, mark, engine_out, profile)` — profit-protection (§30):
  after MFE >= 0.6R, if >45% of the peak is given back AND the engine no longer
  supports the side -> `EXIT_WARNING` (PROTECT ratchets SL to entry+0.3R; >70%
  give-back -> CLOSE). Engine flips to the opposite side at conf >= 55 ->
  `INVALIDATED` CLOSE. Mark <= SL -> `STOPPED` CLOSE.

### Paper engine — `paper_engine.py`
`SmartScalperPaperEngine(profile)`:
- `evaluate(symbols, dry_run=True)` — scan -> `pre_entry_state` for every ranked
  index (audit) -> on the primary, persist the signal + state; if
  `ENTRY_CONFIRMED` and `dry_run=False` and `safeguards.check_entry` passes ->
  `open_trade(strategy='SMART_SCALPER')`. Underlying SL/T are translated to the
  option leg via the observed translation ratio (expected premium move / expected
  index move).
- `manage()` — mark every open SMART_SCALPER trade to the current option LTP,
  `update_trade_price` (hard exits), then `in_trade_state` for PROTECT / CLOSE.
- **Not auto-started** in the app lifespan (spec §48 RuntimeScheduler wiring is
  a follow-on). Callable via endpoints.

### Journal — `journal.py` (spec §43)
`journal()` — from closed `ai_paper_trades WHERE strategy='SMART_SCALPER'` joined
with `smart_scalper_signals`: win rate, PF, expectancy, avg win/loss, avg
R-multiple, max drawdown, avg MFE/MAE, false-signal rate, exit-reason mix —
overall + **by profile / by instrument / by market regime**. No profitability claim.

### API
`GET /api/smart-scalper/{signals, paper/state, paper/positions, paper/journal}` ·
`POST /api/smart-scalper/paper/{evaluate,manage}` (evaluate defaults dry_run=true).

### Tests +6 (`test_smart_scalper_paper.py`)
pre-entry confirms only on all gates; gates block with named reasons;
profit-protection + invalidation + hard-stop transitions; DB helper round-trip +
write-once; journal metrics (win rate / PF / by-profile / by-instrument /
by-regime) from real closed paper trades; static check of no live-order path.
Full suite **423 pass**.

### Verified live (markets closed)
`/paper/journal`, `/paper/positions`, `/signals` -> 200. `POST /paper/evaluate?
dry_run=true&symbols=NIFTY,BANKNIFTY` -> 200, every index `NO_TRADE` (nothing
eligible off-hours), `primary: None`, `live_trading: False`.

---

## 11. SLICE 5 — HISTORICAL REPLAY / BACKTEST (built)

`app/smart_index_scalper/` — strict-causal replay over the **real captured**
`data/market_history.db` (histcap). REQUIRED before any profitability statement
(spec §26). RESEARCH ONLY, no order path, `live_trading` stays false.

| file | role |
|---|---|
| `historical_context.py` | `SessionData(symbol, session, expiry)` loads one captured session once; `.context_at(T)` slices it **causally** with bisect — a candle counts only once `bar_start + timeframe <= T`, a quote only once `received_ts <= T`; ΔOI is DERIVED by differencing vs the snapshot ~5 min earlier (AngelOne never stored `oi_change`). Greeks were never captured → every `*_greeks_source = "UNAVAILABLE"`, no on-the-fly BS. `available_sessions()` = sessions with BOTH candles and a real OI chain. |
| `replay_price_action.py` | causal deriver for `breakout_state / retest_state / reversal_candidate / candle_signals` (the engine takes these as caller inputs — see §7). Pure function of the already-truncated `bars`; HEURISTIC + UNCALIBRATED, exists to exercise the pipeline. |
| `replay.py` | `SmartScalperReplay.run(symbols, step_min, profiles, max_hold_min, profile_overrides)` — walks each session, at each step: `SessionData.context_at` → `MathematicalConfluenceEngine.evaluate` (same engine as live) → `oi_matrix` / `eligibility` / `selection_score` → `option_selector.select` (per profile) → `state_machine.pre_entry_state`. On `ENTRY_CONFIRMED` opens a **simulated** position (in-memory only), marks it bar-by-bar against the REAL historical option LTP, runs `state_machine.in_trade_state`; exits on STOP / TARGET_2 / SM CLOSE / MAX_HOLD / SESSION_END. `profile_overrides` is a labelled calibration knob (spec §26: sweep thresholds) — off by default; when set, `params.gate_mode = "DIAGNOSTIC_SWEEP"`. |
| `replay_metrics.py` | `trade_metrics` (same defs as `journal._metrics`), `reliability` (confidence-bucket → realised win-rate + ECE), `summarize` — overall + by profile / instrument / market-regime. **Sample gate:** below `MIN_SESSIONS=8` OR `MIN_TRADES=20` the aggregate is stamped `descriptive_only` and the calibration table is withheld. |
| `__main__.py` | `python -m app.smart_index_scalper replay-sessions` / `replay [--symbols --step --profile --max-hold]`. |

### Anti-look-ahead
Only closed candles + `received_ts <= T` quotes enter the context; the engine's
swing detector needs `n` bars each side so the last bars are never confirmed
pivots; option fills use the mark at T or later, never an earlier/median price.
Unit-tested (`test_historical_context_is_strictly_causal`,
`test_price_action_is_causal_and_safe_on_short_input`).

### API
`GET /api/smart-scalper/replay/sessions` · `GET /api/smart-scalper/replay`
(`symbols`, `profile`, `step_min`, `max_hold_min`). No write path.

### Tests +7 (`test_smart_scalper_replay.py`)
strict causality of the context slicer; `available_sessions` needs candles+chain;
sim trade open→mark→close lifecycle + metrics + regime grouping + sample gate
(stub engine); replay writes **nothing** to `ai_paper_trades`; no order-path
strings in the replay modules; metrics math (PF / win-rate / max-drawdown /
reliability ECE); causal price-action deriver. Full suite **430 pass**.

### What the replay actually produces today (honest result)
Captured history is **~2–3 partial sessions** (NIFTY / NATURALGAS / CRUDEOIL,
2026-09-02..03; SENSEX has no 1m/5m candles). Run with the **stock profiles** it
opens **0 trades** — the `MathematicalConfluenceEngine` confidence ceiling on
this quiet sample is ~59 and its structural RR-to-T1 (nearest confluence zone)
usually sits below 1.2, so `eligibility` / the profile gates are never all
satisfied. This is a genuine finding, not a harness failure: the pipeline runs
causally over real data and **declines to manufacture trades**. A
`profile_overrides` diagnostic sweep confirms the full open/mark/exit/metrics
path works on the real option LTP series (`descriptive_only`, gate not met).
Meaningful metrics require the forward histcap capture to accumulate sessions.

### Verified
`python -m app.smart_index_scalper replay` → `status: INSUFFICIENT_SAMPLE`,
`sample: {sessions: 3, trades: 0}`, `calibration: INSUFFICIENT_SAMPLE`,
`live_trading: false`, `note` carries "no profitability claim (spec section 26)".

---

## 12. SLICE 6 — FRONTEND: "ADVANCED MATHEMATICAL SCALPER" (built)

New SPA view `#view-mathscalp` (nav + mobile tab "Math Scalper"), rendered by
`loadMathScalp()` in `frontend/static/js/app.js` (vanilla, no build step). Polls
every 8 s while on screen. RESEARCH / PAPER — never a "BUY CE 90%" headline;
every card carries the sub-score breakdown + reasons + what-invalidates + an
UNCALIBRATED disclaimer.

| panel | source | shows |
|---|---|---|
| header | — | profile selector (CONSERVATIVE/BALANCED/AGGRESSIVE), focus-symbol input, UNCALIBRATED disclaimer ("the confluence score is NOT a probability") |
| **Best Opportunity Now** | `GET /api/mathematics/signal` + `ranking.selection.primary` | signal type, direction, confidence, confluence /100, spot, entry zone, stop, T1/T2/T3, RR, option leg (when eligible) |
| **Why this trade? / What invalidates it?** | signal `reason_codes` / `no_trade_reason` + OI walls + battle zone + support/resistance levels | two columns; every reason entity-escaped |
| **sub-score bars** | signal `score_breakdown` | 7 sub-scores as `raw/out_of` mini-bars |
| **Smart Index Ranking** | `GET /api/smart-scalper/ranking?profile=` | eligible #1..n by INDEX_SELECTION_SCORE, then the not-eligible list with the exact failed filters as chips |
| **Mathematical Market Map** | `GET /api/mathematics/market-map` | per index: spot, pivot, Gann balance, nearest S/R, regime, direction, confluence, confidence, signal |
| **Live Market Map** ladder | `GET /api/mathematics/{signal,levels,oi}` | price rungs around spot — PIVOT/R1-3/S1-3, GANN±k, confluence ZONE×n, CALL/PUT walls; SPOT rung highlighted |
| **OI Confluence Matrix** | `GET /api/mathematics/oi` | top strikes by support+resistance+battle score; CE/PE OI (compact) + LTP; PCR + walls + BATTLE ZONE in the header |
| **Historical Replay / Backtest** | `GET /api/smart-scalper/replay?profile=` (on the "Run backtest" button) | status badge (INSUFFICIENT_SAMPLE), sessions/trades vs the gate, win-rate/PF/expectancy/maxDD/calibration, and the by-profile / by-instrument / by-regime table; the "no profitability claim" note is shown verbatim |
| **Paper Journal** | `GET /api/smart-scalper/paper/journal` | overall + by profile / instrument / regime, with the journal's own note |

### Tests
`frontend/tests/render_smoke.test.js` — `loadMathScalp` added to the loader
sweep (now **10 view loaders**); fixtures for the 8 new endpoints incl. a
hostile `reason_codes` payload; asserts the ranking + market-map tables render
and the reason codes are entity-escaped (no live `<img>`). Both FE tests +
backend **430 pass**.

### Auth note
The SPA (and `/static/*`) sits behind the app's basic-auth gate
(`CHANAKYA_ADMIN_USERNAME` / `CHANAKYA_ADMIN_PASSWORD`); only `/api/health` is
exempt. Unchanged by this slice.

---

## 13. Project status — all 6 slices built

math core + engine + API + validation · slice 1 SMART_INDEX_SCALPER ·
slice 2 context/scan · slice 3 option selection · slice 4 DB + paper-trade
state machine + journal · slice 5 strict-causal replay/backtest ·
**slice 6 frontend**. Still RESEARCH / PAPER, `live_trading=false`, no order
path. UNCALIBRATED until the forward histcap capture accumulates a real sample
and the replay clears its 8-session / 20-trade gate.
