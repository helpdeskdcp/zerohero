# ACTIVE arm (AUTOSCALP-ACTIVE) — specification, committed BEFORE any arm signal exists (2026-10-20)

**Why.** The frozen AUTOSCALP has almost stopped trading since its 2026-09-29 filter (7 signals since; last trades 2026-10-14), so the forward test and the arena would have an almost empty AI side ("wins by abstaining"). The ACTIVE arm is a SEPARATE, clearly labelled, shadow-recorded arm with enough activity to measure, and a prospective test of that filter.

**Disclosure.** I have seen the in-sample evidence behind the filter (blocked setups were net losers in the pooled live ledger) and that AUTOSCALP's aggregate gross return is about -0.34%/trade. I expect the ACTIVE arm to do no better per trade than the frozen engine; the purpose is measurement and sample size, not winning. No outcome of the arm exists yet.

## Definition (only difference vs the frozen engine)
- Signals come from the SAME `decide_from_context` call, same inputs, calibration, EV gate and profiles as the live runner, with `config["filters"]` overridden to `{"block_regimes": [], "block_signal_types": [], "block_tod": []}` (the 2026-09-29 blocks removed). SR/chain gates stay OFF as in production.
- Recorded when the ACTIVE decision is BUY_CE or BUY_PE; the frozen engine's own decision at the same moment is stored next to it (`frozen_decision`), so every arm signal is labelled COMMON (frozen engine also took it) or ACTIVE-ONLY (it was blocked by the filter).
- Dedup: at most one arm signal per (underlying, direction) per 10 minutes. Selection when settling (sequential, causal): at most 10 arm trades per IST day; no new arm trade on an underlying while its previous arm trade is still open.
- Own sqlite `data/active_arm.db`, written only by the shadow hook (log only; it never changes the runner's decision, never opens a trade). Kill switch `ACTIVE_ARM_SHADOW=0`.

## Settlement (identical to the arena engine; code in `app/arena/`)
Ask in / bid out on captured quotes, 20 s delay, SL and T1 from the signal, time exit after the signal's `max_hold_sec` (1500 s) via the same EXIT_NOW event type, session-end exit, brokerage/STT/charges, spread model where bid/ask is missing. One lot.

## Reporting and decision (pre-registered)
- Daily in the forward ledger (`candidate_active_arm`): signals, selected trades, settled, net rupees (1 lot), win rate, COMMON vs ACTIVE-ONLY split with trades/day.
- In the arena the arm appears as a separate, labelled participant `ZEROHERO_AI_ACTIVE`; the arena headline AI entrant stays the frozen AUTOSCALP.
- Verdict only when n >= 100 settled trades AND >= 30 distinct days (same rule as the forward test): `PASS_FOR_NEXT_STAGE` iff mean net > 0 with day-clustered one-sided p <= 0.05; else `FAIL`. Until then `ACCUMULATING`. The ACTIVE-ONLY vs COMMON comparison is reported as the prospective test of the filter (does the blocked group really lose?), with its interval; no claim is made below those sample sizes.
- Frozen: any change to the override or the dedup/selection rules starts a new epoch.
