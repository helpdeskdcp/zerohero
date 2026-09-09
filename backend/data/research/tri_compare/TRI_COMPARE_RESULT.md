# TRI-COMPARE — E1 Trend / E2 Structure (ChatGPT) / E3 Hybrid — NIFTY result

_2026-09-09. Research/backtest only; no broker, no live wiring, `live_trading`
untouched. Existing engines (orderflow v1, order_pressure, hcs) NOT modified —
only imported read-only for reuse._

## What was built (deliverables)

| # | deliverable | location |
|---|---|---|
| 1 | three independent engine modules | `engine_trend.py` · `engine_structure.py` · `engine_hybrid.py` |
| 2 | common backtest harness (identical data/tf/cost/slippage/session/risk) | `harness.py` |
| 3 | walk-forward evaluator (expanding folds) | `compare._walk_forward` |
| 4 | ANN comparison layer (TRAIN-only, leak-free) | `ann_layer.py` |
| 5 | unified comparison report | `tri_compare_<stamp>.{md,json}` |
| 6 | GO/NO-GO per engine | in the report |
| 7 | recommended signal-qualification rule | in the report (only emitted if an engine GOes) |
| 8 | full reproducibility config | `config.py` (echoed into the report `config` block) |

Reused validated infra: `orderflow.data` (NIFTY 1m Kaggle → 15m + capability
flags), `orderflow.indicators` (EMA/ATR/ADX/RSI/pivots/efficiency-ratio),
`orderflow.orderflow.build` (structure HH-HL/BOS/CHoCH, regime, value-area,
reclaim), `orderflow.entry_timing` (two-stage SIGNAL→WAITING_FOR_RETEST→
ENTRY_READY + no-chase + entry-quality), `orderflow.exit_engine`, and
`hcs.adaptive.OnlineLogit` + `hcs.adaptive_mc.Isotonic` for the ANN.

## Fair-comparison guarantees

Every engine's ENTRY_READY signal goes through the **identical** harness:
1 pt transaction cost + 1 pt slippage per round trip; session 09:15, no new
entry after 14:45, hard flat 15:15; **one shared risk model** — SL = entry ∓
ATR(14)×1.5, T1/T2/T3 = 1R/2R/3R, 1/3-1/3-1/3 scale-out, SL→BE after +1R, +3R
runner cap; mathematical RR gate = distance-to-nearest-structural-barrier /
risk ≥ 1.3; one position at a time; vol-normalized (every trade risks 1R).
Engines differ **only** in *when* and *which direction*. Split TRAIN 55% / VAL
20% / OOS 25% by session block, chronological; walk-forward 6 expanding folds;
no look-ahead (causal frame, pivots confirmed w bars late), no repaint, rules
frozen before OOS.

## RESULT — NIFTY 15m, 2016–2025, 2599 sessions

| rank | engine | OOS n | win% | OOS expectancy R | PF | maxDD R | Sharpe | composite | verdict |
|---|---|--:|--:|--:|--:|--:|--:|--:|:--:|
| 1 | **E3 HYBRID** | 31 | 51.6 | +0.125 | 1.09 | −3.0 | 2.0 | 25.3 | **NO-GO** |
| 2 | **E1 CLAUDE TREND** | 276 | 39.5 | **−0.214** | 0.67 | −61.0 | −3.3 | 23.4 | **NO-GO** |
| 3 | **E2 STRUCTURE (ChatGPT)** | 60 | 48.3 | **−0.043** | 0.82 | −8.9 | −0.7 | 13.5 | **NO-GO** |

**All three engines fail OOS. No signal is forced.**

## Per-engine diagnosis

### E1 — CLAUDE TREND  (NO-GO, decisively)
- 1057 trades. expR **−0.25R**, PF 0.61, 56% stop-out, only 32% reach T1.
- **Every one of the 10 calendar years is net-negative** (−11R … −41R). TRAIN
  −0.27 / VAL −0.22 / OOS −0.21 — perfectly consistent → **not overfit, the
  edge is simply absent** at 15m after costs.
- Regime: even **TRENDING/HIGH-vol** (the regime it is built for) is −0.11R.
  No regime rescues it.
- Hour: the **09:15–10:00 open hour is the worst** (−0.40R, 117 trades) —
  opening-range breakouts are traps on the index.
- Timeframe sensitivity (E1 only, diagnostic): 15m −0.21R → **1h −0.03R**
  (mild OOS +0.056R, PF 1.14). The trend edge lives at slower horizons; the
  intraday 15m variant is dominated by noise + cost. True daily can't be tested
  with an intraday session/exit harness.

### E2 — PRECISION STRUCTURE / ChatGPT  (NO-GO, but least-bad)
- 242 trades. expR **−0.06R** (near break-even), PF 0.84.
- TRAIN −0.02 (break-even) → **VAL −0.25 (collapses)** → OOS −0.04. The
  VALIDATION failure is the disqualifier: not robust across periods
  (degradation flag = 1.0).
- Asymmetric: **SHORT OOS +0.07R / PF 1.17**, LONG −0.20R / PF 0.48.
- Two-stage entry is very selective: 6016 signals → 1355 ENTRY_READY → 242
  after the RR gate (2901 no-retest timeouts, 1162 no-chase, 913 RR-too-low).

### E3 — HYBRID  (NO-GO, insufficient sample)
- Only **63 trades total, 31 OOS** — below the 40-trade verdict floor.
- OOS looks positive (+0.125R, PF 1.09) but TRAIN −0.10 / VAL −0.40 / OOS +0.12
  are wild small-sample swings, and **ANN filtering makes it worse** (Δ −0.087R).
- "hybrid outperforms both" is **True only because E1 and E2 are both negative
  and E3's tiny lucky sample is positive** — not a real edge. The composite's
  sample-sufficiency factor discounts it; it is not named "best".

### ANN confirmation layer
- Fit TRAIN-only per fold; features are all past-only (no win/pnl/exit in the
  vector — enforced by test). Deterministic given the seed.
- **E1:** ANN Δ **+0.057R** OOS (−0.21 → −0.16), PF 0.67 → 0.77 — a genuine
  mild improvement, but E1 stays negative.
- **E2:** ANN Δ +0.017R — marginal.
- **E3:** ANN Δ −0.087R — hurts.
- Verdict on ANN: **a mild positive filter on E1/E2, not a rescue.** It trims
  the worst trades (top ANN weights: hour, efficiency-ratio, rr-room,
  sr-distance) but cannot turn any engine GO. Keep it as a research filter only.

## Answers to the questions asked

| question | answer |
|---|---|
| Which engine is best standalone? | **None clears GO.** By composite, E1 Trend > E2 Structure (bigger, more consistent sample). E2 is closest to break-even but fails VALIDATION. |
| Which produces the highest-quality signals? | **E2 Structure** — near-break-even expectancy, lowest stop-rate (40% vs E1's 56%), PF 0.84, and a real SHORT-side edge (PF 1.17). But not robust enough. |
| Does Hybrid genuinely outperform both? | **No.** Its apparent outperformance is a 31-trade small-sample artifact; not statistically meaningful. |
| Does ANN genuinely improve OOS? | **Partially** — a real +0.057R on E1 and +0.017R on E2, but it does not make any engine profitable. Research filter only. |
| Minimum conditions to qualify a signal? | **N/A — no engine GOes.** If E2 is pursued: SHORT-only, open-hour or afternoon, in MIXED/MID or TRENDING/LOW-MID regime, RR-room ≥ 1.3, entry-quality ≥ 45, ANN p_win ≥ 0.40. This is descriptive of where its OOS expectancy concentrates, not a validated rule. |

## Overall verdict: **NO-GO for all three.** Diagnosis

1. **15m intraday NIFTY has no persistent directional edge** for a
   trend-breakout OR a structure-retest approach after realistic costs
   (1 pt + 1 pt). Consistent with every prior engine this project has tested.
2. The **trend edge is real but horizon-dependent** — it improves monotonically
   as the timeframe slows (15m → 1h) and would need daily+ bars and a
   swing-style harness to be tested properly.
3. The **structure/ChatGPT engine is the strongest of the three** (near
   break-even, clean SHORT edge) but **fails cross-period robustness**
   (VALIDATION −0.25R).
4. **Hybridising did not manufacture an edge** — it only shrank the sample.
5. **ANN helps at the margin** but is not a substitute for a signal that isn't
   there.

Do NOT wire any of these into live trading or the RK score. If pursued: test
E2 (structure) on 1h with a wider window, SHORT-only, and re-check VALIDATION —
not the 15m long+short form.

_Reproduce: `python -m app.research_engines.tri_compare.compare --tf 15
--start 2016-01-01 --end 2025-12-31 --out data/research/tri_compare`._

---

## ADDENDUM — E2 Structure retest: 1h, SHORT-only, wider window (2010–2025)

Hypothesis from the 15m run: *"E2's SHORT side had PF 1.17 — retest it on 1h,
SHORT-only, over a wider window."* 1h data: 4098 sessions, 2010-01-04 …
2025-12-24 (16 years vs 10). Entry-wait scaled to the 1h bar
(`entry_max_wait_bars` 16→5, `entry_recovery_window` 4→2, no-new-entry 14:15).
Rules frozen; OOS scored once.

### Result: **hypothesis REJECTED — NO-GO**

| slice | n | win% | expR | PF | net R | maxDD R | SL% | max consec losses |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| ALL | 81 | 37.0 | **−0.257** | 0.59 | −20.8 | −21.1 | 60 | 13 |
| TRAIN | 43 | 32.6 | −0.425 | 0.37 | −18.3 | −18.6 | 67 | 13 |
| VAL | 11 | 45.5 | +0.275 | 1.28 | +3.0 | −2.0 | 36 | 2 |
| OOS | 27 | 40.7 | **−0.207** | 0.56 | −5.6 | −6.1 | 59 | 4 |

- **Only 81 trades in 16 years** (~5/yr) — far too sparse; OOS n=27 < the 40 floor.
- Fails every gate: OOS expR ≤ 0, PF 0.56, **0 positive regimes, 0 positive
  calendar years**, VAL (+0.27, n=11) is a lucky pocket between two badly
  negative TRAIN and OOS blocks.
- Walk-forward (6 folds, 11 trades each): +0.07 / **−0.72** / **−0.60** / +0.28
  / +0.05 / −0.22 → no stability.
- ANN: INSUFFICIENT (43 train trades).

**Conclusion:** the 15m "SHORT PF 1.17" was a **60-trade small-sample artifact**.
It does not survive (a) a slower, cleaner timeframe, (b) a 16-year window with
more regimes, or (c) isolating the SHORT side. Removing the LONG side made E2
*worse* (−0.26R vs the combined −0.06R at 15m) — the LONG side was diluting
noise, not hiding a SHORT edge.

### Incidental finding (both-sides, 1h, 2010–2025)

E1 CLAUDE TREND on 1h over the wide window: **OOS expR +0.012R, PF 1.05** —
the least-bad trend result in the whole study, i.e. essentially break-even.
Consistent with "the trend edge lives at slower horizons", but break-even is
not an edge, and ANN hurts it (Δ −0.041R). Still NO-GO. A genuine test of the
trend hypothesis needs **daily bars + an overnight-capable (swing) harness** —
the intraday session/exit machinery here cannot represent that.

### Net

No variant of any of the three engines clears GO. The structure engine's
apparent SHORT edge was noise. Pursuing E2 further is not warranted; a real
next step for the trend hypothesis is a separate **daily swing harness**, not
more intraday parameter variants.
