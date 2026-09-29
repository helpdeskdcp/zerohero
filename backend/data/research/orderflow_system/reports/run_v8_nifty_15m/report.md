> **WARNING: OHLCV PROXY MODE -- order-flow values (delta/cvd/absorption/imbalance) are APPROXIMATIONS derived from price+volume shape, NOT true bid/ask aggressor data. See data/source_ohlcv_proxy.py's module docstring for the exact formulas.**

# Backtest Report

## Manifest
```json
{
  "generated_at": "2026-09-29T12:57:06.016560+00:00",
  "git_commit": "65f6ba99222af1d8a3d85b453c67ae57a0b80e2a",
  "config_version": "v8",
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
  "n": 2,
  "verdict": "INSUFFICIENT_SAMPLE",
  "win_rate": 0.0,
  "expectancy_r": -0.7192,
  "profit_factor_gross": 0.05,
  "profit_factor_net": 0.0,
  "max_drawdown_r": 1.414,
  "sharpe": -1.035,
  "sortino": -1.035,
  "avg_win_r": null,
  "avg_loss_r": -0.7192,
  "avg_mfe_r": 0.4472,
  "avg_mae_r": 0.6546,
  "avg_holding_bars": 1.5,
  "total_r_gross": -0.95,
  "total_r_net": -1.438
}
```

## Per-setup
```json
{
  "SETUP3_BULL_REVERSAL": {
    "n": 1,
    "verdict": "INSUFFICIENT_SAMPLE",
    "win_rate": 0.0,
    "expectancy_r": -0.0244,
    "profit_factor_gross": null,
    "profit_factor_net": 0.0,
    "max_drawdown_r": 0.0,
    "sharpe": null,
    "sortino": null,
    "avg_win_r": null,
    "avg_loss_r": -0.0244,
    "avg_mfe_r": 0.6123,
    "avg_mae_r": 0.1011,
    "avg_holding_bars": 2.0,
    "total_r_gross": 0.05,
    "total_r_net": -0.024
  },
  "SETUP4_BEAR_REVERSAL": {
    "n": 1,
    "verdict": "INSUFFICIENT_SAMPLE",
    "win_rate": 0.0,
    "expectancy_r": -1.414,
    "profit_factor_gross": 0.0,
    "profit_factor_net": 0.0,
    "max_drawdown_r": 0.0,
    "sharpe": null,
    "sortino": null,
    "avg_win_r": null,
    "avg_loss_r": -1.414,
    "avg_mfe_r": 0.2821,
    "avg_mae_r": 1.2082,
    "avg_holding_bars": 1.0,
    "total_r_gross": -1.0,
    "total_r_net": -1.414
  }
}
```

## Break-even win-rate PASS/FAIL

| Setup | RR | n | Actual WR | Pure BE WR | Cost-adj BE WR | Margin | Result |
|---|---|---|---|---|---|---|---|
| SETUP3_BULL_REVERSAL | 2.0 | 1 | 0.0% | 33.3% | 35.8% | -35.8% | **FAIL** |
| SETUP3_BULL_REVERSAL | 3.0 | 1 | 0.0% | 25.0% | 26.9% | -26.9% | **FAIL** |
| SETUP4_BEAR_REVERSAL | 2.0 | 1 | 0.0% | 33.3% | 47.1% | -47.1% | **FAIL** |
| SETUP4_BEAR_REVERSAL | 3.0 | 1 | 0.0% | 25.0% | 35.4% | -35.4% | **FAIL** |

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