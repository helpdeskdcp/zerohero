> **WARNING: OHLCV PROXY MODE -- order-flow values (delta/cvd/absorption/imbalance) are APPROXIMATIONS derived from price+volume shape, NOT true bid/ask aggressor data. See data/source_ohlcv_proxy.py's module docstring for the exact formulas.**

# Backtest Report

## Manifest
```json
{
  "generated_at": "2026-09-29T12:41:13.988106+00:00",
  "git_commit": "58cf75ef40645ccba4bcccefca4789013b2102bf",
  "config_version": "v6",
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
  "n": 8,
  "verdict": "INSUFFICIENT_SAMPLE",
  "win_rate": 0.5,
  "expectancy_r": 0.0757,
  "profit_factor_gross": 1.594,
  "profit_factor_net": 1.131,
  "max_drawdown_r": 3.276,
  "sharpe": 0.052,
  "sortino": 0.631,
  "avg_win_r": 1.3108,
  "avg_loss_r": -1.1594,
  "avg_mfe_r": 1.5362,
  "avg_mae_r": 1.2943,
  "avg_holding_bars": 18.38,
  "total_r_gross": 2.375,
  "total_r_net": 0.606
}
```

## Per-setup
```json
{
  "SETUP3_BULL_REVERSAL": {
    "n": 7,
    "verdict": "INSUFFICIENT_SAMPLE",
    "win_rate": 0.4286,
    "expectancy_r": 0.0556,
    "profit_factor_gross": 1.344,
    "profit_factor_net": 1.084,
    "max_drawdown_r": 3.276,
    "sharpe": 0.036,
    "sortino": 0.463,
    "avg_win_r": 1.6755,
    "avg_loss_r": -1.1594,
    "avg_mfe_r": 1.4255,
    "avg_mae_r": 1.1041,
    "avg_holding_bars": 19.43,
    "total_r_gross": 1.375,
    "total_r_net": 0.389
  },
  "SETUP4_BEAR_REVERSAL": {
    "n": 1,
    "verdict": "INSUFFICIENT_SAMPLE",
    "win_rate": 1.0,
    "expectancy_r": 0.2169,
    "profit_factor_gross": null,
    "profit_factor_net": null,
    "max_drawdown_r": 0.0,
    "sharpe": null,
    "sortino": null,
    "avg_win_r": 0.2169,
    "avg_loss_r": null,
    "avg_mfe_r": 2.3114,
    "avg_mae_r": 2.6253,
    "avg_holding_bars": 11.0,
    "total_r_gross": 1.0,
    "total_r_net": 0.217
  }
}
```

## Break-even win-rate PASS/FAIL

| Setup | RR | n | Actual WR | Pure BE WR | Cost-adj BE WR | Margin | Result |
|---|---|---|---|---|---|---|---|
| SETUP3_BULL_REVERSAL | 2.0 | 7 | 42.9% | 33.3% | 38.0% | +4.8% | **PASS** |
| SETUP3_BULL_REVERSAL | 3.0 | 7 | 42.9% | 25.0% | 28.5% | +14.3% | **PASS** |
| SETUP4_BEAR_REVERSAL | 2.0 | 1 | 100.0% | 33.3% | 59.4% | +40.6% | **PASS** |
| SETUP4_BEAR_REVERSAL | 3.0 | 1 | 100.0% | 25.0% | 44.6% | +55.4% | **PASS** |

## Walk-forward folds

| Train | Test | n | Expectancy (R) | Verdict |
|---|---|---|---|---|
| 2022-04-09..2023-04-17 | 2023-04-18..2023-07-17 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2022-07-08..2023-07-17 | 2023-07-18..2023-10-17 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2022-10-11..2023-10-17 | 2023-10-18..2024-01-18 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2023-01-11..2024-01-18 | 2024-01-19..2024-04-23 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2023-04-18..2024-04-23 | 2024-04-24..2024-07-24 | 3 | -1.0919 | INSUFFICIENT_SAMPLE |
| 2023-07-18..2024-07-24 | 2024-07-25..2024-10-23 | 1 | 0.2517 | INSUFFICIENT_SAMPLE |
| 2023-10-18..2024-10-23 | 2024-10-24..2025-01-24 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2024-01-19..2025-01-24 | 2025-01-27..2025-04-30 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2024-04-24..2025-04-30 | 2025-05-02..2025-07-29 | 1 | 0.2169 | INSUFFICIENT_SAMPLE |
| 2024-07-25..2025-07-29 | 2025-07-30..2025-10-30 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2024-10-24..2025-10-30 | 2025-10-31..2026-02-01 | 0 | 0.0 | INSUFFICIENT_SAMPLE |

## Disclaimer

This system is for RESEARCH and EDUCATION only. Backtested results are historical and do NOT guarantee future performance. Order-flow data on NSE for retail traders is often delayed or proxy-based; OHLCV proxy mode produces APPROXIMATIONS, not true order flow. All performance figures are net of modelled costs but real slippage may differ. This is NOT investment advice. Consult a SEBI-registered advisor before trading real capital. Risk is the user's own.