# ORDERFLOW_ENGINE v1 — NIFTY — build + backtest result

_2026-09-09. Built to `backend/ORDERFLOW_ENGINE_SPEC.md`. Research/backtest only;
no broker, no live wiring, `live_trading` untouched._

## Architecture delivered (the four separate questions)

```
Market Data (NIFTY 1m Kaggle -> 5m)
  -> order-flow FEATURE layer      orderflow.build()      L2-L4 of the spec
       flow_proxy / cvd_proxy (PRESSURE_PROXY, NOT delta) · structure BOS/CHoCH/
       retest · regime (ADX/ATR%/efficiency) · vwap price-proxy · TPO value area ·
       structural reclaim (sweep/exhaustion) proxy
  -> RK SCORE  (Q2: how strong)    signal.rk_score()      0-100, capability-aware
       additive, transparent, weight redistributed for N/A categories
  -> SIGNAL DIRECTION (Q1)         signal.detect()        LONG/SHORT/NONE, fresh flips only
  -> ENTRY TIMING ENGINE (Q3)      entry_timing.run()     TWO-STAGE, never "signal==entry"
       SIGNAL_DETECTED -> WAITING_FOR_RETEST -> ENTRY_READY | EXPIRED_{TIMEOUT,
       INVALIDATED, NOCHASE}
       - signal LEG = last opposing pivot -> signal bar ; range = H - L
       - retracement zones tested: 0 / 10 / 20 / 30 / 40 / 50 / 60 % of the leg
       - pullback into zone -> momentum-recovery bar -> NO-CHASE filter
         (extension vs signal origin / VWAP / 60-baseline / 20-EMA) -> RR check
       - ENTRY_QUALITY_SCORE 0-100 (8 weighted, non-double-counted components)
  -> RISK/REWARD validation        reject RR-to-T1 < min_rr (1.3)
  -> EXIT ENGINE (Q4)              exit_engine.simulate() 1/3 @T1, 1/3 @T2, SL->BE
       after T1, runner to T3 / cap / session-flat ; SL-before-target intrabar
  -> LEARNING DATASET              orderflow_v1_learning_*.jsonl  (spec: every
       signal + full path: signal_price/high/low, retracement, MFE/MAE, entry,
       SL, T1/T2/T3, outcome, time-to-target/SL, regime, RK/OFQ/EQ scores)
  -> OPTION MAP (separate layer)   option_map.map_to_option()  advisory only;
       index direction + optimal index entry decided first; premium noise never
       feeds back into the index signal
```

Calibration is **TRAIN -> VALIDATION -> OOS** by chronological session blocks
(55 / 20 / 25 %). The optimal retracement zone z* is fitted on TRAIN, must also
clear a VALIDATION gate (net > 0, PF >= 1.10, expR > 0.02), OOS scored once.
Nothing is hard-coded — `entry_zone_frac` is overwritten by the calibrator.

## Result

### 5-minute (primary), NIFTY 2016-2025, 2599 sessions

| slice | n | win% | expR | PF | net pts |
|---|--:|--:|--:|--:|--:|
| TRAIN | 170 | 46.5 | +0.164 | 1.18 | +363 |
| **VALIDATION** | 62 | 38.7 | **−0.098** | **0.73** | **−314** |
| **OOS** | 62 | 37.1 | **−0.093** | **0.81** | **−253** |
| ALL | 294 | 42.9 | +0.055 | 0.96 | −204 |

**No retracement zone survives the TRAIN + VALIDATION gate.** The zone
calibration table shows the disagreement plainly: TRAIN prefers a ~40% pullback,
VALIDATION prefers ~0-10%, and they invert on OOS — i.e. there is **no stable
"optimal entry zone"**, the pullback depth is noise. z* = 0.3 is reported only
as the least-bad TRAIN zone.

### 15-minute (reference)

72 trades total / 16 OOS (below the verdict floor). OOS expR −0.017, PF 0.71,
net −108. Also NO-GO.

## Verdict: **NO-GO / NOT VALIDATED**

- TRAIN-positive, VALIDATION-negative, OOS-negative — the classic overfit-to-train
  signature, no real edge.
- An earlier configuration with far-away measured-move targets printed a GO
  (OOS PF 1.47) but T1 was hit only 17 % of the time and 100 %+ of the net came
  from two outlier years (2020, 2024) via session-flat/trail mechanics, not from
  the signal. Fixing the targets to reachable levels and enforcing an honest
  VALIDATION gate collapsed it to NO-GO. That episode is why the VALIDATION
  split and reachable targets are non-negotiable here.
- This matches `ORDERFLOW_ENGINE_SPEC.md` §0: a cash-index engine with **no true
  order flow** (no aggressor side, no volume, only PRESSURE_PROXY) has no
  exploitable directional edge on NIFTY.

## What v1 nonetheless delivers (kept for reuse)

- A working, causal, deterministic **two-stage entry architecture** (SIGNAL !=
  ENTRY) with a calibrated retracement engine, no-chase filter, RR gate, and a
  0-100 Entry-Quality score — reusable by any future strategy that *does* have an
  edge.
- The **learning dataset** format for continuous zone re-calibration.
- The **capability-honest** feature layer (every proxy method-tagged; nothing
  called delta/CVD/footprint).
- The TRAIN/VAL/OOS + walk-forward harness with an outlier-year-aware verdict.

## Only path to a v2 with real order flow

Persist Angel mode-3 **SnapQuote on the NIFTY future** (`app/l2capture/` has the
parser) for >= 40-60 independent sessions, then rebuild §2-§7 (aggressor
classification, buy/sell volume, delta, CVD, footprint, imbalance) on the future
— snapshot-sampled, but real. Until then this stays NO-GO.
