> **WARNING: OHLCV PROXY MODE -- order-flow values (delta/cvd/absorption/imbalance) are APPROXIMATIONS derived from price+volume shape, NOT true bid/ask aggressor data. See data/source_ohlcv_proxy.py's module docstring for the exact formulas.**

# Backtest Report

## Manifest
```json
{
  "generated_at": "2026-09-29T09:21:44.657314+00:00",
  "git_commit": "1960b387496c683f058af939c3514fc8d6e7b1b9",
  "config_version": "v4",
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
  "n": 24,
  "verdict": "INSUFFICIENT_SAMPLE",
  "win_rate": 0.25,
  "expectancy_r": -1.1878,
  "profit_factor_gross": 0.722,
  "profit_factor_net": 0.196,
  "max_drawdown_r": 28.431,
  "sharpe": -0.758,
  "sortino": -1.574,
  "avg_win_r": 1.1568,
  "avg_loss_r": -1.9694,
  "avg_mfe_r": 1.385,
  "avg_mae_r": 1.5619,
  "avg_holding_bars": 2.79,
  "total_r_gross": -4.696,
  "total_r_net": -28.508
}
```

## Per-setup
```json
{
  "SETUP1_BULL_BREAKOUT": {
    "n": 19,
    "verdict": "INSUFFICIENT_SAMPLE",
    "win_rate": 0.2105,
    "expectancy_r": -1.3593,
    "profit_factor_gross": 0.652,
    "profit_factor_net": 0.148,
    "max_drawdown_r": 25.29,
    "sharpe": -0.89,
    "sortino": -1.673,
    "avg_win_r": 1.1189,
    "avg_loss_r": -2.0201,
    "avg_mfe_r": 1.3806,
    "avg_mae_r": 1.5689,
    "avg_holding_bars": 2.47,
    "total_r_gross": -4.82,
    "total_r_net": -25.826
  },
  "SETUP2_BEAR_BREAKOUT": {
    "n": 5,
    "verdict": "INSUFFICIENT_SAMPLE",
    "win_rate": 0.4,
    "expectancy_r": -0.5364,
    "profit_factor_gross": 1.041,
    "profit_factor_net": 0.479,
    "max_drawdown_r": 1.989,
    "sharpe": -0.347,
    "sortino": -2.737,
    "avg_win_r": 1.2324,
    "avg_loss_r": -1.7156,
    "avg_mfe_r": 1.4021,
    "avg_mae_r": 1.5352,
    "avg_holding_bars": 4.0,
    "total_r_gross": 0.123,
    "total_r_net": -2.682
  }
}
```

## Break-even win-rate PASS/FAIL

| Setup | RR | n | Actual WR | Pure BE WR | Cost-adj BE WR | Margin | Result |
|---|---|---|---|---|---|---|---|
| SETUP1_BULL_BREAKOUT | 2.0 | 19 | 21.1% | 33.3% | 70.2% | -49.1% | **FAIL** |
| SETUP1_BULL_BREAKOUT | 3.0 | 19 | 21.1% | 25.0% | 52.6% | -31.6% | **FAIL** |
| SETUP2_BEAR_BREAKOUT | 2.0 | 5 | 40.0% | 33.3% | 52.0% | -12.0% | **FAIL** |
| SETUP2_BEAR_BREAKOUT | 3.0 | 5 | 40.0% | 25.0% | 39.0% | +1.0% | **PASS** |

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
| 2024-10-24..2025-10-30 | 2025-10-31..2026-02-01 | 7 | -0.8594 | INSUFFICIENT_SAMPLE |

## Disclaimer

This system is for RESEARCH and EDUCATION only. Backtested results are historical and do NOT guarantee future performance. Order-flow data on NSE for retail traders is often delayed or proxy-based; OHLCV proxy mode produces APPROXIMATIONS, not true order flow. All performance figures are net of modelled costs but real slippage may differ. This is NOT investment advice. Consult a SEBI-registered advisor before trading real capital. Risk is the user's own.