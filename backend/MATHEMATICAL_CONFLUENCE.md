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
