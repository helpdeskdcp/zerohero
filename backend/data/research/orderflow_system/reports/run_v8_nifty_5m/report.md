> **WARNING: OHLCV PROXY MODE -- order-flow values (delta/cvd/absorption/imbalance) are APPROXIMATIONS derived from price+volume shape, NOT true bid/ask aggressor data. See data/source_ohlcv_proxy.py's module docstring for the exact formulas.**

# Backtest Report

## Manifest
```json
{
  "generated_at": "2026-09-29T12:55:45.647311+00:00",
  "git_commit": "65f6ba99222af1d8a3d85b453c67ae57a0b80e2a",
  "config_version": "v8",
  "data_mode": "ohlcv_proxy",
  "is_proxy": true,
  "symbol": "NIFTY",
  "timeframe": "5m",
  "date_range": [
    "2022-04-09",
    "2026-09-28"
  ],
  "seed": 42,
  "in_sample_range": [
    "2022-04-09 11:20:00+05:30",
    "2026-03-30 15:25:00+05:30"
  ],
  "oos_range": [
    "2026-04-01 09:15:00+05:30",
    "2026-09-28 15:25:00+05:30"
  ]
}
```

## Global
```json
{
  "n": 17,
  "verdict": "INSUFFICIENT_SAMPLE",
  "win_rate": 0.4118,
  "expectancy_r": 0.118,
  "profit_factor_gross": 2.525,
  "profit_factor_net": 1.524,
  "max_drawdown_r": 2.049,
  "sharpe": 0.139,
  "sortino": 0.291,
  "avg_win_r": 0.8329,
  "avg_loss_r": -0.3825,
  "avg_mfe_r": 0.6856,
  "avg_mae_r": 0.4691,
  "avg_holding_bars": 25.47,
  "total_r_gross": 4.218,
  "total_r_net": 2.005
}
```

## Per-setup
```json
{
  "SETUP3_BULL_REVERSAL": {
    "n": 12,
    "verdict": "INSUFFICIENT_SAMPLE",
    "win_rate": 0.3333,
    "expectancy_r": -0.1256,
    "profit_factor_gross": 0.958,
    "profit_factor_net": 0.428,
    "max_drawdown_r": 1.492,
    "sharpe": -0.312,
    "sortino": -0.385,
    "avg_win_r": 0.2816,
    "avg_loss_r": -0.3292,
    "avg_mfe_r": 0.4377,
    "avg_mae_r": 0.4681,
    "avg_holding_bars": 16.67,
    "total_r_gross": -0.074,
    "total_r_net": -1.507
  },
  "SETUP4_BEAR_REVERSAL": {
    "n": 5,
    "verdict": "INSUFFICIENT_SAMPLE",
    "win_rate": 0.6,
    "expectancy_r": 0.7025,
    "profit_factor_gross": 5.292,
    "profit_factor_net": 3.949,
    "max_drawdown_r": 1.179,
    "sharpe": 0.56,
    "sortino": 1.205,
    "avg_win_r": 1.5679,
    "avg_loss_r": -0.5956,
    "avg_mfe_r": 1.2807,
    "avg_mae_r": 0.4714,
    "avg_holding_bars": 46.6,
    "total_r_gross": 4.292,
    "total_r_net": 3.513
  }
}
```

## Break-even win-rate PASS/FAIL

| Setup | RR | n | Actual WR | Pure BE WR | Cost-adj BE WR | Margin | Result |
|---|---|---|---|---|---|---|---|
| SETUP3_BULL_REVERSAL | 2.0 | 12 | 33.3% | 33.3% | 37.3% | -4.0% | **FAIL** |
| SETUP3_BULL_REVERSAL | 3.0 | 12 | 33.3% | 25.0% | 28.0% | +5.3% | **PASS** |
| SETUP4_BEAR_REVERSAL | 2.0 | 5 | 60.0% | 33.3% | 38.5% | +21.5% | **PASS** |
| SETUP4_BEAR_REVERSAL | 3.0 | 5 | 60.0% | 25.0% | 28.9% | +31.1% | **PASS** |

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
| 2024-10-24..2025-10-30 | 2025-10-31..2026-02-01 | 6 | 0.092 | INSUFFICIENT_SAMPLE |

## Disclaimer

This system is for RESEARCH and EDUCATION only. Backtested results are historical and do NOT guarantee future performance. Order-flow data on NSE for retail traders is often delayed or proxy-based; OHLCV proxy mode produces APPROXIMATIONS, not true order flow. All performance figures are net of modelled costs but real slippage may differ. This is NOT investment advice. Consult a SEBI-registered advisor before trading real capital. Risk is the user's own.