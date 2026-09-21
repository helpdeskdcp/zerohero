# Baseline Report — 2026-09-21

Real run, this session, immediately before any of the 27-phase spec's later
implementation phases would begin (none were started — this task was
audit-only per its own directive).

## Test suite

Command: `bash scripts/run_tests.sh` (repo's own venv-preferring pytest wrapper).

```
1669 passed in 322.78s (0:05:22)
```

0 failed, 0 skipped, 0 warnings surfaced in the tail output. This is the full
suite, not a subset.

## Safety state (read-only check, not modified)

`.env` has no `LIVE_TRADING`, `CHANAKYA_ALLOW_LIVE`, or
`CHANAKYA_LIVE_CONFIRM_TOKEN` lines set — all three gates for live order
execution remain at their unset/false defaults.

## Backtest baseline

No new backtests were run for this report — per the task's own scope
(audit + baseline only, no implementation), the existing real backtest
evidence already on file is the baseline:

| Candidate | Verdict | Report |
|---|---|---|
| SSL Hybrid PRO | NO-GO (NIFTY/BANKNIFTY 2 periods each, NATURALGAS) | `data/research/ssl_hybrid_pro/SSL_HYBRID_PRO_FINAL_VERDICT.md` |
| 3-engine comparison (Claude Trend/ChatGPT Structure/Hybrid) | NO-GO | `data/research/tri_compare/TRI_COMPARE_RESULT.md` |
| Trend-swing (daily/overnight) | NO-GO, 0/6 walk-forward folds | `data/research/trend_swing/TREND_SWING_RESULT.md` |
| Liquidity Sweep (SMC) | NOT READY, 0/38 features, joint AUC 0.54 | `LIQUIDITY_SWEEP_FINAL_REPORT.md` (repo root) |
| Bollinger Bands breakout confirmation | PROMISING, not GO (n=16-18, 0-cost model on NIFTY) | `data/research/ta_options_pdf_upgrade/VERDICT.md` |
| IV-vs-Realized-Vol entry gate | NO-GO | `data/research/ta_options_pdf_upgrade/VERDICT.md` |
| StochRSI+Supertrend | NO-GO (code since removed) | memory `stochrsi-supertrend-nogo.md` only — no file on disk |
| NIFTY weekly Iron Condor | NO-GO (code since removed) | memory `nifty-weekly-ic-intraday-nogo.md` only — no file on disk |

Net effect: the currently-live `state_classifier.py` 9-component formula has
survived every real-data challenger tested against it this session. No
candidate has beaten baseline; the walk-forward/train-test-swap discipline
this session already applies is why each was correctly rejected rather than
deployed.

## Known unfixed defect (carried forward, not re-derived here)

`calibration-overconfidence-k8.md` (memory, re-checked 2026-09-11, n=122):
ECE 0.185, Brier 0.302, PF 0.618, expectancy -1.04 pts/trade, reliability
inverts at high confidence (0.8-0.9 bin = 12.5% actual win rate). No fix
applied to date. See `SYSTEM_INVENTORY.md` §11 for the exact code location
(`app/engines/scalp_strategy.py:172` `_score_to_prob`,
`app/autoscalp/calibration_report.py:51` `calibration_report`).

## Conclusion

The test suite is green and the deterministic engine is the survivor of
this session's own real-data validation gauntlet, not an unvalidated
baseline. Any future phase of the 27-phase spec that proposes changing
`state_classifier.py`'s weights or `scalp_strategy.py`'s blend ratio needs
to clear this same bar (real backtest + train/test swap) before being
considered anything other than research.
