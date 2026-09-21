# NATURALGAS Setup Dedup Audit & Calibration Contamination Check — 2026-09-21

Read-only audit + one additive fix, per the user's "do not blindly modify
existing modules" directive. `scalp_strategy.py`, `app/ai/fusion.py`, and
`autoscalp/runner.py`'s trading logic were **not touched**.

## Phase 1 — Audit (real data, `data/chanakya.db`)

**The reported "7 wins" were never real trades.** They were the Claude
coordinator's own ad-hoc, throwaway shell-script grouping of
`shadow_decisions` rows by exact `(entry, stop_loss, target_1)` tuple —
never a feature of `shadow_outcome.py` itself. Real numbers:

- **16 raw `shadow_decisions` rows** for NATURALGAS between 09:52-10:00 UTC
  (the reported "surge" window; the real chain actually starts 09:50:13).
- Every single row: `deterministic_decision = BUY_PE`, same `option_token`
  (578349), same `strike` (275.0), same `expiry` (23SEP2026) — one
  contract, continuously re-evaluated as price climbed and entry/target
  drifted slightly tick to tick (which is exactly why tuple-grouping still
  overcounted: it split identical opportunities into "distinct" setups
  purely because the recomputed premium changed by a few paise).
- `fused_final_state` alternated `NO_TRADE` / `WEAK_SELL` on **every** one
  of the 16 ticks — AI/fusion vetoed this signal the entire time. It never
  reached a tradeable state (`BUY`/`STRONG_BUY`/`SELL`/`STRONG_SELL`).
- **Cross-checked `scalp_signals` (the real paper-trade table) for
  NATURALGAS, entire day: zero rows.** No real paper trade ever opened for
  this episode. The "7/7 TARGET_HIT" reported to the user was tracking a
  purely hypothetical deterministic plan the live system correctly never
  acted on.

**Is `_maybe_recalibrate()`'s training data contaminated by this?** No.
`app/backtest/calibration.py`'s live refit (`autoscalp/runner.py::
_maybe_recalibrate`) trains on `db.list_scalp_signals(source="LIVE",
status="CLOSED", ...)` — `scalp_signals`, a completely separate table from
`shadow_decisions`, populated only by REAL paper-trade opens/closes. Real
`scalp_signals` rows for today (whole file, all symbols):

```
SENSEX   BUY_PE  entry=350.05  LOSS  (TIME exit, -1.1 pts)
SENSEX   BUY_PE  entry=354.05  LOSS  (TIME exit, -31.0 pts)
CRUDEOIL BUY_PE  entry=719.4   WIN   (TARGET, +7.4 pts)
```

Three trades, clean non-overlapping `entry_ts`/`exit_ts` lifecycles — no
duplication. `autoscalp/runner.py:767-777` gates every entry through
`safeguards.check_entry(..., open_keys=open_keys, ...)`, and
`app/autoscalp/safeguards.py:135` — `if (underlying, side) in open_keys:
<reject>` — is a real, already-existing, working "don't open a second
position on the same underlying+side while one is open" guard. This
explains why the real trade count (3) stayed small and clean while the
shadow-observation log (which has no such gate, by design — it's meant to
log every tick for comparison research) produced 16 rows for one episode.
**Today's calibration fix (commit `04136c7`) was built on valid, non-
duplicated data.** This module (`app/ai/shadow_outcome.py`) was the only
thing miscounting, and only in its own reporting, never in anything that
feeds a live decision.

Note (out of scope, flagged not chased): the two real SENSEX trades'
entries (350.05, 354.05) don't match any SENSEX `shadow_decisions` entry
seen today (which cluster 278-311) — the real strategy engine's option-leg
resolution apparently sometimes differs from what shadow-mode observes.
Worth a separate look if it matters; not investigated here.

## Phase 2 — Fix (additive, `app/ai/shadow_outcome.py` only)

Added `group_into_setups()` + `setups_report()` (new functions;
`outcomes_report()` and `check_outcome()` are unchanged, same contract, so
nothing that already calls them breaks). A setup is a contiguous run of
ticks sharing symbol + deterministic direction + resolved option leg
(`option_token`, or `strike`+`expiry` when the token is absent), with no
real `NO_TRADE`/no-leg tick and no gap >120s (4x the default 30s
`decide_every_sec`) breaking it. `fused_final_state` oscillating on the
SAME leg does NOT break the chain (that's AI confidence noise on one
opportunity, not a new one) — only the deterministic engine's own
decision/leg identity does. The outcome is evaluated from the run's FIRST
tick (the moment the opportunity was actually detected), not a later,
drifted tick.

Also added `_real_trade_match()`: cross-checks `scalp_signals` for a real
trade in the setup's time window, so every setup now honestly reports
whether it was ever actually traded, not just what the deterministic plan
would have hypothetically done.

## Phase 6/7-lite — Before vs after, real data

| Symbol | Naive count (tuple-grouped, what the user was told) | Corrected (`setups_report`) | Real trade? |
|---|---|---|---|
| NATURALGAS | 7 "TARGET_HIT" | **1 setup**, 20 evaluations, TARGET_HIT | **None — never real-traded** |
| SENSEX | 6 (3 "OPEN" + 3 "SL_HIT") | **3 setups** (7, 2, 9 evaluations), all SL_HIT | None matched (see note above) |
| CRUDEOIL | 1 TARGET_HIT | **1 setup**, 3 evaluations, TARGET_HIT | **Matched — real WIN, +7.4 pts** |
| NIFTY | 0 | 0 | — |

No edge claim is made either way — this is a corrected count, not a
verdict. CRUDEOIL's single real setup did convert to a real, winning
trade; NATURALGAS's did not exist as a real trade at all despite the
premium genuinely running from ~4.0 to 5.9 that session.

## Phase 8 — NO_TRADE reason codes

Already satisfied by existing infrastructure — `shadow_decisions.
reason_codes` (populated by `app/ai/fusion.py`) already carries this, e.g.
real NATURALGAS NO_TRADE rows today show `["deterministic_no_trade"]`, and
AI-vetoed rows carry codes like `ai_signal_validation_fail`,
`ai_orderflow_conflict`. Nothing new needed.

## Phase 10/11 — Module safety, AI authority

No existing module was modified, disabled, or deleted.
`app/ai/fusion.py`'s downgrade-only authority (AI can only weaken toward
NO_TRADE, never originate or upgrade a signal) was read, not touched —
still true, unaffected by this work.

## Tests

10 new tests in `tests/test_shadow_outcome.py` (chain-collapsing,
fused-state-doesn't-break-chain, real-NO_TRADE-does-break-chain,
opposite-direction/different-leg/large-gap all break the chain,
first-tick-used-for-outcome, real-trade-match found/not-found, end-to-end
via `setups_report`). Full suite: 1685/1685 passed (1675 baseline + 10 new).
