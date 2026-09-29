> **WARNING: OHLCV PROXY MODE -- order-flow values (delta/cvd/absorption/imbalance) are APPROXIMATIONS derived from price+volume shape, NOT true bid/ask aggressor data. See data/source_ohlcv_proxy.py's module docstring for the exact formulas.**

# Backtest Report

## Manifest
```json
{
  "generated_at": "2026-09-29T12:52:22.184888+00:00",
  "git_commit": "65f6ba99222af1d8a3d85b453c67ae57a0b80e2a",
  "config_version": "v7",
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
  "n": 0,
  "verdict": "INSUFFICIENT_SAMPLE",
  "expectancy_r": 0.0
}
```

## Per-setup
```json
{}
```

## Break-even win-rate PASS/FAIL

| Setup | RR | n | Actual WR | Pure BE WR | Cost-adj BE WR | Margin | Result |
|---|---|---|---|---|---|---|---|

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