> **WARNING: OHLCV PROXY MODE -- order-flow values (delta/cvd/absorption/imbalance) are APPROXIMATIONS derived from price+volume shape, NOT true bid/ask aggressor data. See data/source_ohlcv_proxy.py's module docstring for the exact formulas.**

# Backtest Report

## Manifest
```json
{
  "generated_at": "2026-09-29T03:49:09.021555+00:00",
  "git_commit": "7a5dfc12b2586478f2c1b6a62d5645c5665d3576",
  "config_version": "v3",
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
  "n": 29,
  "verdict": "INSUFFICIENT_SAMPLE",
  "win_rate": 0.2759,
  "expectancy_r": -1.0778,
  "profit_factor_gross": 0.789,
  "profit_factor_net": 0.239,
  "max_drawdown_r": 30.72,
  "sharpe": -0.671,
  "sortino": -1.502,
  "avg_win_r": 1.227,
  "avg_loss_r": -1.9558,
  "avg_mfe_r": 1.3793,
  "avg_mae_r": 1.5063,
  "avg_holding_bars": 3.86,
  "total_r_gross": -4.196,
  "total_r_net": -31.257
}
```

## Per-setup
```json
{
  "SETUP1_BULL_BREAKOUT": {
    "n": 22,
    "verdict": "INSUFFICIENT_SAMPLE",
    "win_rate": 0.2273,
    "expectancy_r": -1.2577,
    "profit_factor_gross": 0.728,
    "profit_factor_net": 0.189,
    "max_drawdown_r": 27.134,
    "sharpe": -0.787,
    "sortino": -1.604,
    "avg_win_r": 1.2892,
    "avg_loss_r": -2.0068,
    "avg_mfe_r": 1.348,
    "avg_mae_r": 1.5611,
    "avg_holding_bars": 3.77,
    "total_r_gross": -4.32,
    "total_r_net": -27.67
  },
  "SETUP2_BEAR_BREAKOUT": {
    "n": 7,
    "verdict": "INSUFFICIENT_SAMPLE",
    "win_rate": 0.4286,
    "expectancy_r": -0.5123,
    "profit_factor_gross": 1.031,
    "profit_factor_net": 0.484,
    "max_drawdown_r": 3.429,
    "sharpe": -0.342,
    "sortino": -2.936,
    "avg_win_r": 1.1233,
    "avg_loss_r": -1.7391,
    "avg_mfe_r": 1.4778,
    "avg_mae_r": 1.3341,
    "avg_holding_bars": 4.14,
    "total_r_gross": 0.123,
    "total_r_net": -3.586
  }
}
```

## Break-even win-rate PASS/FAIL

| Setup | RR | n | Actual WR | Pure BE WR | Cost-adj BE WR | Margin | Result |
|---|---|---|---|---|---|---|---|
| SETUP1_BULL_BREAKOUT | 2.0 | 22 | 22.7% | 33.3% | 68.7% | -46.0% | **FAIL** |
| SETUP1_BULL_BREAKOUT | 3.0 | 22 | 22.7% | 25.0% | 51.5% | -28.8% | **FAIL** |
| SETUP2_BEAR_BREAKOUT | 2.0 | 7 | 42.9% | 33.3% | 51.0% | -8.1% | **FAIL** |
| SETUP2_BEAR_BREAKOUT | 3.0 | 7 | 42.9% | 25.0% | 38.2% | +4.6% | **PASS** |

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
| 2024-10-24..2025-10-30 | 2025-10-31..2026-02-01 | 10 | -0.5561 | INSUFFICIENT_SAMPLE |

## Disclaimer

This system is for RESEARCH and EDUCATION only. Backtested results are historical and do NOT guarantee future performance. Order-flow data on NSE for retail traders is often delayed or proxy-based; OHLCV proxy mode produces APPROXIMATIONS, not true order flow. All performance figures are net of modelled costs but real slippage may differ. This is NOT investment advice. Consult a SEBI-registered advisor before trading real capital. Risk is the user's own.