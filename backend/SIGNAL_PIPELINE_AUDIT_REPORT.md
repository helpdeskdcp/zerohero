# Signal Pipeline Audit -- scalp_strategy.decide_from_context

READ-ONLY audit. No production file was modified, no threshold was changed, nothing was re-armed. Every function called below is the existing, unmodified production code (`app.engines.scalp_strategy.decide_from_context`, `app.backtest.replay.SimTrade`/`ReplayContext`, `app.backtest.oi_history_adapter`).

## Dataset

- Source: `/root/oi_dashboard/oi_history.db` (real NIFTY option-chain cycles, 35 real days, 2026-07-13..2026-08-28) -- the same archive that produced the P6/P6.1 filter-ablation numbers cited in scalp_strategy.py's own comments.
- TRAIN (calibration fit only): 2026-07-13..2026-08-10 -- 51 samples, avg_win=8.817, avg_loss=5.605
- OOS/TEST (everything below): 2026-08-11..2026-08-28
- Raw market states in OOS window: 7122
- Throttled decision calls captured (decide_every_sec=60.0): 3430
- Config: PRODUCTION DEFAULTS, unchanged (no filters/thresholds overridden)

## Observability gap found while building this audit

`decide_from_context()`'s `out_none()` helper hardcodes `direction="NONE"` in its base dict, and most rejection paths' `extra` dict never overrides it -- even though a real direction was already computed internally to pick CE vs PE. Confirmed by reading the source (not inferred): **967 of 3349 rejected candidates (28.9% of non-entry decisions)** had a real `signal_type` (so a real directional read existed) but reported `direction: "NONE"` in the live output. This script reconstructs it from `signal_type` (via the same BULLISH/BEARISH sets `state_classifier.py` itself defines) to do this audit at all -- but any OTHER consumer of this dict (dashboard, Telegram alert, this audit if it hadn't special-cased it) sees a blank direction for the majority of rejections. Separately: NO rejection path (not even EV gate, confidence-LOW WATCH, or the option-quality gate, all of which run AFTER a contract is already selected internally) includes the selected strike/entry/stop_loss/target in its return value -- only a successful ENTRY decision does. That blocks grading near-miss rejections against a REAL option-premium outcome; this audit falls back to an index-direction proxy for every rejection stage as a result (see Caveats).

## Funnel -- where candidates are lost

| Stage | Count | % of all decision calls |
|---|---|---|
| STATE_NONE | 1112 | 32.4% |
| CE_PE_CONFLICT | 696 | 20.3% |
| HARD_FILTER_SIGNAL_TYPE | 563 | 16.4% |
| HARD_FILTER_TOD | 362 | 10.6% |
| HARD_FILTER_REGIME | 345 | 10.1% |
| MTF_GATE | 172 | 5.0% |
| EV_GATE | 99 | 2.9% |
| ENTRY | 81 | 2.4% |

**Final entries generated (BUY_CE/BUY_PE): 81 / 3430 (2.36%)**

## Rejected-candidate classification (A/B/C/D)

A = correct rejection (no real directional read existed at all). B = false rejection / missed profitable signal. C = correctly rejected a losing signal. D = cannot determine (no forward data).

| Category | Count | % of rejected candidates |
|---|---|---|
| A) Correct rejection | 1112 | 33.2% |
| B) False rejection | 1087 | 32.5% |
| C) Correctly rejected loss | 1150 | 34.3% |
| D) Cannot determine | 0 | 0.0% |

## Per-stage breakdown -- profitable vs losing signals removed by each gate

| Stage | No real signal (A) | False rejection -- WOULD HAVE WON (B) | Correctly rejected loss (C) | Cannot determine (D) |
|---|---|---|---|---|
| STATE_NONE | 1112 | **0** | 0 | 0 |
| CE_PE_CONFLICT | 0 | **385** | 311 | 0 |
| HARD_FILTER_SIGNAL_TYPE | 0 | **270** | 293 | 0 |
| HARD_FILTER_TOD | 0 | **177** | 185 | 0 |
| HARD_FILTER_REGIME | 0 | **140** | 205 | 0 |
| MTF_GATE | 0 | **50** | 122 | 0 |
| EV_GATE | 0 | **65** | 34 | 0 |

## Top 5 signal-processing problems (ranked by evidence)

1. **Observability gap: direction + contract identity missing from rejection output** -- 967/3349 (28.9%) of rejected candidates had a real directional read that the output dict reported as "NONE"; zero rejection paths carry the selected strike/entry/SL/target. This is why every other finding below relies on an index-direction proxy instead of a real option outcome -- fix this FIRST and every other number in this report gets sharper for free, with no threshold change.
2. **CE_PE_CONFLICT** -- 385 of 696 candidates rejected at this gate would have been WINS (hypothetical), vs 311 correctly-rejected losses. Ratio: 385/696 of determinable outcomes were false rejections.
3. **HARD_FILTER_SIGNAL_TYPE** -- 270 of 563 candidates rejected at this gate would have been WINS (hypothetical), vs 293 correctly-rejected losses. Ratio: 270/563 of determinable outcomes were false rejections.
4. **HARD_FILTER_TOD** -- 177 of 362 candidates rejected at this gate would have been WINS (hypothetical), vs 185 correctly-rejected losses. Ratio: 177/362 of determinable outcomes were false rejections.
5. **HARD_FILTER_REGIME** -- 140 of 345 candidates rejected at this gate would have been WINS (hypothetical), vs 205 correctly-rejected losses. Ratio: 140/345 of determinable outcomes were false rejections.

## Caveats (read before acting on any number above)

- Pre-option-selection rejections (no clean state, S/R unavailable, hard filters, MTF gate, CE/PE conflict, no tradeable contract) are graded with an **index-direction proxy** (>=1 ATR forward move in the read direction within 12 bars), NOT a real option P&L -- no contract was ever selected at that pipeline stage to price. This is a directional-correctness check, not a trading-profitability check.
- This script's code CAN grade a rejection with a real historical option-premium replay of the exact locked contract (via SimTrade, the same logic the real backtest engine uses) -- but in practice this path never fires for ANY rejection stage, including ones that run after a contract is selected internally (option quality, EV gate, chain-bias veto, LOW-confidence WATCH), because `decide_from_context()` never includes the selected strike/entry/stop_loss/target in a NO_TRADE/WATCH return value (see the Observability Gap section above). Every rejection in this report is graded with the coarser index-direction proxy as a result.
- This is ONE 35-day real dataset, ONE symbol (NIFTY), ONE decide_every_sec setting. No multiple-comparison correction was applied to this ranking -- treat it as a map of WHERE to investigate further, not a final verdict on any single gate.
- No threshold, filter, or config was changed to produce this report. No re-arming was done.

Raw per-candidate dump (all 3430 rows, full captured fields): `data/research/signal_pipeline_audit/oos_candidates.json`