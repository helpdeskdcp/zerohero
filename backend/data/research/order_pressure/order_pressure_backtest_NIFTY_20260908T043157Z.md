# Multi-Candle Order-Pressure Engine — backtest

_generated 2026-09-08T04:10:41.215155+00:00 · symbol NIFTY · tfs 5m · 2022-01-01..2023-07-01 · runtime 1275.8s_

## VERDICT: **NO-GO**

Do NOT wire into production. Failing components listed above. This matches the prior audits: on this problem a small NN does not beat simple baselines out-of-sample.

Failed components:
- 5m: model beats base-rate in only 0 regime(s) (<2)

## Data coverage

```
"SPOT next-candle model: real walk-forward OOS possible (NIFTY 1m 2015-2025, BANKNIFTY 1m). CE/PE pressure + OI + dynamic trade management: DEMONSTRATOR ONLY -- 5-10 poll sessions per symbol, no dense option candles. Any option-side OOS claim is INSUFFICIENT_SAMPLE."
```
Unavailable (reported, not invented):
- Per-1m option OHLC/OI candles -- do not exist. Option history is poll-snapshot only.
- Upstox expired-instruments historical candles -- stored responses have empty candle arrays.
- MCX (CRUDEOIL/NATURALGAS) SPOT 1m history -- not in Kaggle; only option snapshots (~5-7 days).
- Cash-index traded volume -- always 0; SPOT pressure uses price geometry only.
- Level-2 / aggressor / order-flow tape -- not present anywhere (see orderflow-l2 memory).

## Timeframe 5m

rows 29543 · sessions 389 · labels {'UP': 0.3962, 'DOWN': 0.3742, 'INSIDE': 0.2296, 'n': 29543}

| model | set | folds | acc | macroF1 | logloss | brier | ece | fit s |
|---|---|--:|--:|--:|--:|--:|--:|--:|
| majority | core | 5 | 0.3899 | 0.187 | 1.0818 | 0.6558 | 0.0084 | 0.1 |
| persistence | core | 5 | 0.3891 | 0.2945 | 5.3503 | 0.9868 | 0.3736 | 0.3 |
| net_pressure_logit | core | 5 | 0.3761 | 0.2909 | 1.085 | 0.655 | 0.0183 | 31.8 |
| multinomial_logit | core | 5 | 0.3687 | 0.2711 | 1.0745 | 0.654 | 0.0358 | 114.5 |
| gb_stumps | full | 5 | 0.4583 | 0.4427 | 1.2627 | 0.6084 | 0.0205 | 844.0 |
| mlp | core | 5 | 0.3943 | 0.3507 | 1.0703 | 0.6467 | 0.0226 | 78.5 |

**OOS best model:** {'name': 'mlp', 'accuracy': 0.3943, 'macro_f1': 0.3507, 'log_loss': 1.0703, 'brier': 0.6467, 'ece': 0.0226, 'n': 6297}
**OOS best baseline:** {'name': 'majority', 'accuracy': 0.3899, 'macro_f1': 0.187, 'log_loss': 1.0818, 'n': 6297}

### per regime (single 70/30 split)

| regime | n | acc | macroF1 | base-rate acc |
|---|--:|--:|--:|--:|
| TREND_UP | 1747 | 0.4081 | 0.2675 | 0.419 |
| CHOP | 374 | 0.3824 | 0.2906 | 0.4198 |
| RANGE | 5375 | 0.3697 | 0.2792 | 0.37 |
| TREND_DOWN | 1367 | 0.406 | 0.323 | 0.4148 |

### dynamic trade management (OOS entries from the winning model)

eval entries: 4168

| policy | n | win% | exp R | PF | maxDD R | MAE R | MFE R |
|---|--:|--:|--:|--:|--:|--:|--:|
| fixed_sl | 4168 | 0.4427 | 0.1488 | 1.297 | -86.377 | -1.062 | 1.297 |
| early_exit | 4168 | 0.5137 | 0.0998 | 1.249 | -64.93 | -0.924 | 1.117 |
| trailing_sl | 4168 | 0.4352 | 0.1371 | 1.311 | -72.567 | -0.996 | 1.252 |

early-exit improvement: -0.049 R/trade
SL avoidance: {"fixed_sl_stops": 2075, "stops_avoided_by_early_exit": 454, "avoided_and_better": 454, "recovered_before_sl_rate_fixed": 0.464}

## Option demonstrator (CRUDEOIL / NATURALGAS, 5m poll snapshots)

```
{
  "CRUDEOIL": {
    "CE": {
      "status": "USABLE_DEMO",
      "bars": 574,
      "sessions": 5,
      "mean_net_pressure": -8.8,
      "oi_state_mix": {
        "SHORT_BUILDING": 0.005,
        "SHORT_COVERING": 0.002,
        "NONE": 0.993
      },
      "note": "descriptive only -- sample far below any OOS threshold"
    },
    "PE": {
      "status": "USABLE_DEMO",
      "bars": 574,
      "sessions": 5,
      "mean_net_pressure": -2.24,
      "oi_state_mix": {
        "SHORT_BUILDING": 0.002,
        "NONE": 0.998
      },
      "note": "descriptive only -- sample far below any OOS threshold"
    }
  },
  "NATURALGAS": {
    "CE": {
      "status": "USABLE_DEMO",
      "bars": 632,
      "sessions": 5,
      "mean_net_pressure": 0.0,
      "oi_state_mix": {
        "NONE": 1.0
      },
      "note": "descriptive only -- sample far below any OOS threshold"
    },
    "PE": {
      "status": "USABLE_DEMO",
      "bars": 633,
      "sessions": 5,
      "mean_net_pressure": 0.0,
      "oi_state_mix": {
        "NONE": 1.0
      },
      "note": "descriptive only -- sample far below any OOS threshold"
    }
  }
}
```
*Option-side sample is 5–7 poll sessions per symbol → descriptive only, no predictive / OOS claim possible.*
