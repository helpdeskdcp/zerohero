# High-Conviction Runner — forward test log

Running READ-ONLY forward test of the HCR strategy on live NIFTY 5m sessions, to
see whether the backtest behaviour (`IMBALANCE_NEXT_CANDLE_1R3_MTF_OI.md` §HCR:
Upstox close-green 65 %, blended E[R] +0.40, PF 2.45, ~1.4 signals/month) shows
up out-of-sample on data captured *after* the strategy was written.

- Record a session:  `venv/bin/python scripts/hcr_forward_test.py [YYYY-MM-DD]`
- Machine log:        `backend/data/hcr_forward_test.jsonl` (git-ignored, append-only)
- Also visible in the UI: **Research → High-Conviction Runner** (forward-test line)

**HCR is a rare-signal strategy (~1.5/month), so most sessions are a no-fire.**
A no-fire day is a valid observation, not a failure. A real forward-test verdict
needs **dozens of *fired* signals across ≥ 2 volatility regimes** — nowhere close
yet. Nothing here is `PROVEN`; the strategy is still a RANGE-SHAPE proxy on the
non-directly-tradable NIFTY cash index.

## Observations

| # | session | DoW | day range / 20d-median (wide ≥1.3×) | max range_x <14:00 | HCR fired? | result | note |
|--:|---|---|---|--:|:--:|---|---|
| 1 | 2026-09-07 | Mon | 151.3 / 134.1 (need ≥ 174.4) → **not wide** | 2.21 (need ≥ 3.0) | **no** | — | quiet chop day; both the wide-day and the spike filter said no. Matches the design (sit out quiet days). Session captured 09:15–15:00 IST. |

## Tally

- Sessions observed: **1**
- HCR fired: **0**
- Graded fired days: **0**
- Verdict: **too early** — need many more sessions, and specifically fired ones.
