> **WARNING: OHLCV PROXY MODE -- order-flow values (delta/cvd/absorption/imbalance) are APPROXIMATIONS derived from price+volume shape, NOT true bid/ask aggressor data. See data/source_ohlcv_proxy.py's module docstring for the exact formulas.**

# Backtest Report

## Manifest
```json
{
  "generated_at": "2026-09-29T12:35:24.977216+00:00",
  "git_commit": "dd19e1a7a2b5105b65fe153d7eaa248e167244d4",
  "config_version": "v5",
  "data_mode": "ohlcv_proxy",
  "is_proxy": true,
  "symbol": "NIFTY",
  "timeframe": "15m",
  "date_range": [
    "2022-04-09",
    "2026-09-28"
  ],
  "seed": 42,
  "in_sample_range": [
    "2022-04-09 11:15:00+05:30",
    "2026-03-30 15:15:00+05:30"
  ],
  "oos_range": [
    "2026-04-01 09:15:00+05:30",
    "2026-09-28 15:15:00+05:30"
  ]
}
```

## Global
```json
{
  "n": 14,
  "verdict": "INSUFFICIENT_SAMPLE",
  "win_rate": 0.4286,
  "expectancy_r": -0.2388,
  "profit_factor_gross": 0.719,
  "profit_factor_net": 0.53,
  "max_drawdown_r": 5.342,
  "sharpe": -0.285,
  "sortino": -0.597,
  "avg_win_r": 0.6286,
  "avg_loss_r": -0.8893,
  "avg_mfe_r": 0.7924,
  "avg_mae_r": 0.6909,
  "avg_holding_bars": 8.21,
  "total_r_gross": -1.722,
  "total_r_net": -3.343
}
```

## Per-setup
```json
{
  "SETUP3_BULL_REVERSAL": {
    "n": 11,
    "verdict": "INSUFFICIENT_SAMPLE",
    "win_rate": 0.4545,
    "expectancy_r": -0.342,
    "profit_factor_gross": 0.588,
    "profit_factor_net": 0.436,
    "max_drawdown_r": 5.015,
    "sharpe": -0.389,
    "sortino": -3.089,
    "avg_win_r": 0.5812,
    "avg_loss_r": -1.1114,
    "avg_mfe_r": 0.8324,
    "avg_mae_r": 0.793,
    "avg_holding_bars": 8.64,
    "total_r_gross": -2.392,
    "total_r_net": -3.762
  },
  "SETUP4_BEAR_REVERSAL": {
    "n": 3,
    "verdict": "INSUFFICIENT_SAMPLE",
    "win_rate": 0.3333,
    "expectancy_r": 0.1397,
    "profit_factor_gross": 2.998,
    "profit_factor_net": 1.939,
    "max_drawdown_r": 0.12,
    "sharpe": 0.269,
    "sortino": 1.357,
    "avg_win_r": 0.8654,
    "avg_loss_r": -0.2231,
    "avg_mfe_r": 0.6456,
    "avg_mae_r": 0.3164,
    "avg_holding_bars": 6.67,
    "total_r_gross": 0.67,
    "total_r_net": 0.419
  }
}
```

## Break-even win-rate PASS/FAIL

| Setup | RR | n | Actual WR | Pure BE WR | Cost-adj BE WR | Margin | Result |
|---|---|---|---|---|---|---|---|
| SETUP3_BULL_REVERSAL | 2.0 | 11 | 45.5% | 33.3% | 37.5% | +8.0% | **PASS** |
| SETUP3_BULL_REVERSAL | 3.0 | 11 | 45.5% | 25.0% | 28.1% | +17.3% | **PASS** |
| SETUP4_BEAR_REVERSAL | 2.0 | 3 | 33.3% | 33.3% | 36.1% | -2.8% | **FAIL** |
| SETUP4_BEAR_REVERSAL | 3.0 | 3 | 33.3% | 25.0% | 27.1% | +6.2% | **PASS** |

## Walk-forward folds

| Train | Test | n | Expectancy (R) | Verdict |
|---|---|---|---|---|
| 2022-04-09..2023-04-17 | 2023-04-18..2023-07-17 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2022-07-08..2023-07-17 | 2023-07-18..2023-10-17 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2022-10-11..2023-10-17 | 2023-10-18..2024-01-18 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2023-01-11..2024-01-18 | 2024-01-19..2024-04-23 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2023-04-18..2024-04-23 | 2024-04-24..2024-07-24 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2023-07-18..2024-07-24 | 2024-07-25..2024-10-23 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2023-10-18..2024-10-23 | 2024-10-24..2025-01-24 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2024-01-19..2025-01-24 | 2025-01-27..2025-04-30 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2024-04-24..2025-04-30 | 2025-05-02..2025-07-29 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2024-07-25..2025-07-29 | 2025-07-30..2025-10-30 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2024-10-24..2025-10-30 | 2025-10-31..2026-02-01 | 0 | 0.0 | INSUFFICIENT_SAMPLE |

## Disclaimer

This system is for RESEARCH and EDUCATION only. Backtested results are historical and do NOT guarantee future performance. Order-flow data on NSE for retail traders is often delayed or proxy-based; OHLCV proxy mode produces APPROXIMATIONS, not true order flow. All performance figures are net of modelled costs but real slippage may differ. This is NOT investment advice. Consult a SEBI-registered advisor before trading real capital. Risk is the user's own.