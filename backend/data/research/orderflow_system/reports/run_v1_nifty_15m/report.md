> **WARNING: OHLCV PROXY MODE -- order-flow values (delta/cvd/absorption/imbalance) are APPROXIMATIONS derived from price+volume shape, NOT true bid/ask aggressor data. See data/source_ohlcv_proxy.py's module docstring for the exact formulas.**

# Backtest Report

## Manifest
```json
{
  "generated_at": "2026-09-29T02:26:28.836128+00:00",
  "git_commit": "ad83a4c429c1708195f86fb05869b68a79cd5543",
  "config_version": "v1",
  "data_mode": "ohlcv_proxy",
  "is_proxy": true,
  "symbol": "NIFTY",
  "timeframe": "15m",
  "date_range": [
    "2021-09-29",
    "2026-09-28"
  ],
  "seed": 42,
  "in_sample_range": [
    "2021-09-29 09:15:00+05:30",
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
| 2021-09-29..2022-09-23 | 2022-09-26..2022-12-27 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2021-12-29..2022-12-27 | 2022-12-28..2023-03-28 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2022-03-28..2023-03-28 | 2023-03-29..2023-07-03 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2022-06-24..2023-07-03 | 2023-07-04..2023-10-03 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2022-09-26..2023-10-03 | 2023-10-04..2024-01-04 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2022-12-28..2024-01-04 | 2024-01-05..2024-04-05 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2023-03-29..2024-04-05 | 2024-04-08..2024-07-09 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2023-07-04..2024-07-09 | 2024-07-10..2024-10-09 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2023-10-04..2024-10-09 | 2024-10-10..2025-01-10 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2024-01-05..2025-01-10 | 2025-01-13..2025-04-15 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2024-04-08..2025-04-15 | 2025-04-16..2025-07-15 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2024-07-10..2025-07-15 | 2025-07-16..2025-10-15 | 0 | 0.0 | INSUFFICIENT_SAMPLE |
| 2024-10-10..2025-10-15 | 2025-10-16..2026-01-16 | 0 | 0.0 | INSUFFICIENT_SAMPLE |

## Disclaimer

This system is for RESEARCH and EDUCATION only. Backtested results are historical and do NOT guarantee future performance. Order-flow data on NSE for retail traders is often delayed or proxy-based; OHLCV proxy mode produces APPROXIMATIONS, not true order flow. All performance figures are net of modelled costs but real slippage may differ. This is NOT investment advice. Consult a SEBI-registered advisor before trading real capital. Risk is the user's own.