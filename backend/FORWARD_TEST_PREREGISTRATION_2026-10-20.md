# Forward test pre-registration (frozen rules) — 2026-10-20

**Purpose.** Replace in-sample arguments with a forward, tamper-evident test of the autonomous engine. Written before any forward trade exists (epoch starts 2026-10-21).

- **Population:** `ai_paper_trades`, `strategy='AUTOSCALP'`, `status='CLOSED'`, opened on/after the epoch start. MANUAL rows, SCALP and ZTH are excluded.
- **Frozen artifacts:** sha256 of the rule-bearing sources listed in `app/forward_test/ledger.py::FROZEN_FILES` + `rules_engine/policy.yaml`, stored in `data/forward_test/frozen_manifest.json`. If any hash changes, the daily run writes an `EPOCH_BREAK` ledger line and restarts the count from that date; results never span a rule change. (The rules engine runs in SHADOW only; it does not change trades.)
- **Outcome:** unit-free premium return minus MODELLED round-trip costs (index options: formula estimate with 0.75 pt/leg slippage; MCX NG/CRUDE: validated per-lot profile + 0.5 pt). Instruments without a cost model are skipped and counted. Costs are modelled, not contract-note values.
- **Ledger:** `data/forward_test/ledger.jsonl`, one hash-chained line per run (`hash = sha256(prev_hash + canonical record)`); `verify_chain()` detects edits/deletions. Cron: weekdays 23:50 IST (after MCX close).
- **Decision (evaluated only when n >= 100 AND distinct days >= 30 inside one epoch):** `PASS_FOR_NEXT_STAGE` iff mean net return > 0, day-clustered one-sided p <= 0.05, AND Wilson 95% lower bound of win rate (net>0) > break-even win rate. Otherwise `FAIL`. Before that: `ACCUMULATING`, and **no performance claim of any kind is made**.
- **Never:** compare with "SEBI trainers", call anything "proven", or publish a probability not backed by calibration. A PASS only licenses a longer, larger forward paper stage.
- **Baseline expectation (disclosed, not a result):** the in-sample ledger shows AUTOSCALP gross ~ -0.34%/trade and modelled net negative (see CLAIM_TEST_RESULT_2026-10-20.md); the forward test is expected to FAIL unless the engine changes.


## Addendum (2026-10-20, before the epoch started)
`rules_engine/policy.yaml` was removed from the frozen-file list: it is a SHADOW policy that does not influence AUTOSCALP's trades, so editing it must not restart the epoch. The ledger now also records **Candidate V2** (the same AUTOSCALP trades filtered by the unified policy v1.0.0; a subset, evaluated alongside, never replacing the frozen engine). Its thresholds are those in policy.yaml as of this commit (max 6 trades/day, 3 consecutive losses, 15-min cooldown, RR>=1.3, premium>=8, session windows).
