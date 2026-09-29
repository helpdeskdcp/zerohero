# Changelog

Every entry records what changed, why, the params before/after, and links
to the backtest result that justified the change. No rule change is
"done" without numbers attached (spec U3) -- an entry with no result link
means the change hasn't been validated yet and must not be treated as
champion.

## v1 -- 2026-09-29

**Change:** initial implementation. All 4 setups (bull/bear breakout,
bull/bear reversal) coded exactly per the rulebook's own suggested default
thresholds -- no tuning applied.

**Rationale:** establish a systematic, versioned baseline before any
parameter search, per spec U1/U4 ("a change that only improves in-sample is
rejected" implies you need an honest, untuned starting point to compare
against).

**Params:** see `config/v1.yaml` (== `config/base.yaml` defaults, no
overrides yet).

**Backtest result:** `reports/run_v1_nifty_5m/` (official, event-driven,
Mode C proxy) + `reports/run_v1_nifty_15m/` (cross-check timeframe). Run
2026-09-29 against real NIFTY INDEX data, 2021-09-29 to 2026-09-28
(oos_holdout_days=180), git commit `ad83a4c429c1`.

- **5-min (official):** in-sample n=2 trades (both SETUP1_BULL_BREAKOUT,
  both stopped out), expectancy -2.28R net of real futures-equivalent
  costs. OOS holdout (last 180 days, untouched): n=0. 13 walk-forward folds,
  effectively all empty. SETUP2/3/4: zero qualifying trades over the full
  5 years.
- **15-min (cross-check):** n=0 across all 4 setups, all periods.
- **Sensitivity screen** (setup1, delta_mult x stop_buffer_ticks, 9-point
  grid): n=2 at every grid point (delta_mult isn't the binding constraint --
  the CVD-new-high + absorption confirmation conjunction is), expectancy
  negative at every point tested, **robustness_score = 0.0**.
- Two real implementation bugs were found and fixed while producing this
  result (not to manufacture trades, but because the literal rulebook
  produced *zero* signals across all 4 setups before fixing them): (1)
  requiring CVD-new-high AND absorption on the exact same bar as the
  breakout candle is close to self-contradictory (a wide-range breakout bar
  is rarely also a narrow-range absorption bar) -- both are now checked
  over the approach window (retest_bars back through the trigger) instead;
  (2) the rulebook's suggested "tick" thresholds (1-2) were calibrated for
  a small-tick instrument, not the NIFTY index (avg 5-min bar range ~22.8
  points) -- rescaled to NIFTY points. Both changes are documented inline
  in config/base.yaml and strategy/_breakout.py.

**Interpretation:** this literal rulebook, on 5 years of real NIFTY 5m/15m
index data, produces far too few qualifying setups to draw any conclusion
either way (INSUFFICIENT_SAMPLE throughout, per spec's own 30-trade
threshold) -- it is neither validated NOR conclusively rejected. The
honest finding is that the four simultaneous confirmation conditions,
coded faithfully, are individually common but their conjunction is
extremely rare on this instrument/timeframe. This is consistent with this
codebase's broader prior evidence (tick-rule CVD-vs-SMA, L2 imbalance, SSL
Hybrid, liquidity-sweep SMC -- all separately NO-GO on NIFTY/BANKNIFTY).

**Status:** CHAMPION (first version; nothing to compare against yet; not
"adopted" in any profitability sense -- there is no sample to adopt).

## v2 -- 2026-09-29 (CHALLENGER, REJECTED)

**Change:** widened setup1's confirmed bottleneck only (not a blind global
loosening, and not `delta_mult` -- v1's own sensitivity sweep already
proved delta_mult irrelevant, n=2 at every value tested):
- `setup1_bull_breakout.retest_bars`: 3 -> 6
- `setup1_bull_breakout.cvd_new_high_lookback`: 20 -> 10
- `proxy.absorption_vol_mult`: 2.0 -> 1.5 (data-layer, affects all setups)

**Rationale:** v1's funnel analysis found the real bottleneck was
CVD-new-high and absorption almost never co-occurring even within the
approach window -- these three parameters directly target that, and only
that.

**Backtest result:** `reports/run_v2_nifty_5m/`. In-sample: n=2 (unchanged
from v1 -- same 2 trades). OOS (untouched holdout): n=1 (up from v1's n=0),
expectancy -1.12R net (a loser). 13 walk-forward folds, 0 positive.

**Acceptance check** (`engine.walkforward.champion_vs_challenger`):
`adopted=False`. OOS expectancy dropped 100% vs champion (0 -> negative,
worse than the `oos_expectancy_drop_max_pct=25.0` limit); 0/13 folds
positive vs the required minimum. **REJECTED** -- v1 remains champion.

**Interpretation:** even a principled widening aimed directly at the
confirmed bottleneck only surfaced ONE additional trade across 9+ years of
data (in-sample was completely unaffected), and it lost. This is not
"the widening was wrong" -- it's further evidence that the rulebook's
four-condition conjunction is structurally too rare on NIFTY 5-min to ever
reach a decidable sample within a reasonable parameter range, not a
threshold-calibration problem.

**Status:** REJECTED. Champion remains v1.

## v3 -- 2026-09-29 (CHALLENGER, REJECTED -- corrected re-run, see bug note)

**Change:** V-a structural variant (score-based confirm, `min_confirmations: 3`
of 4) for all 4 setups, chosen directly from the funnel/ablation diagnostic
(`reports/funnel_diagnostic.json`) rather than another blind widen:
- Breakout (1/2): CVD-new-high/low fires on only 6.3-9.4% of trigger bars,
  absorption on 9.9-13.8% -- individually common conditions (delta_ok,
  vol_ok ~89-96%) were being gated out by requiring BOTH rare conditions
  simultaneously. `min_confirmations=3` needs only one of the two.
- Reversal (3/4): `delta_flip_ok` (ALL of `lt_delta_flip_bars`+1 bars
  same-signed) fired on **0 of 150-211 reclaims in 3 of 4 setup/timeframe
  combinations** -- an "ALL of N" condition is structurally brittle to a
  single noise bar.

**CRITICAL BUG found and fixed after the first v3 run (before this entry was
finalized):** a zero-volume degenerate-comparison bug. Any threshold check of
the form `x >= mult * rolling_mean(x)` silently degenerates to the
trivially-true `0 >= mult*0` whenever the rolling baseline itself is exactly
zero for a whole window -- exactly the case for NIFTY INDEX's Dhan-attached
`volume` field before **2022-04-09** (real, nonzero volume covers only
76,889 of 928,805 total 1-min bars, ~8%, and only from that date onward).
Verified directly: a real 2017-06-08 bar with `volume=delta=vol_ma_setup=
avg_delta_abs=0.0` was still passing `delta_ok=True, vol_ok=True,
absorption_ok=True` -- a "confirmed signal" manufactured from a data desert,
not real order flow. **This means the FIRST v3 run's n=363 in-sample count
was contaminated** -- an unknown but evidently large fraction of those
"trades" were fake. Fixed with `has_real_volume` guards (rolling baseline
must be `> 0` before the multiplicative comparison is evaluated) in three
places: `data/source_ohlcv_proxy.py` (`is_absorption`/`is_imbalance`),
`strategy/_breakout.py::confirm_breakdown()` (`delta_ok`/`vol_ok`),
`strategy/_reversal.py::confirm_breakdown()` (`vol_ok`). Verified fixed (0
confirmations in the 2017 zero-volume window, was previously nonzero) and
the full test suite still passes (46/46, no regressions). `data.start_date`
in `config/base.yaml` moved to `2022-04-09` (the real volume floor) as a
pure efficiency measure -- 2017-2022 now correctly yields zero confirmations
anyway, so excluding it changes nothing but saves compute.
`DataSource.coverage_report()` now reports `nonzero_volume_pct`/
`nonzero_volume_first`/`nonzero_volume_last` so this is visible on every
future run, not silently assumed.

**Backtest result (CORRECTED re-run, post-fix):** `reports/run_v3_nifty_5m/`.
**n=29 in-sample** (down from the contaminated run's n=363 -- roughly 92%
of the original "trades" were fake, bug-manufactured confirmations from the
zero-volume desert, not real signal). n=22 OOS (unchanged -- OOS window is
2026-04 to 2026-09, entirely inside the real-volume era, so it was never
contaminated). Both IS and OOS now fall under the 30-trade G1 minimum, so
the formal verdict is **INSUFFICIENT_SAMPLE**, not a decisive reject on
sample-size grounds alone -- but every other signal available says the same
thing the contaminated run said, only more starkly:
- Global: win_rate=27.6%, expectancy=**-1.08R net** (IS), **-1.29R net**
  (OOS), PF_gross=0.79 (already <1 before any cost), PF_net=0.24,
  max_drawdown=30.7R, Sharpe=-0.67.
- SETUP1_BULL_BREAKOUT (n=22): expectancy -1.26R, PF_net 0.19, FAILS
  cost-adjusted breakeven WR at both RR=2 (22.7% actual vs 68.7% required)
  and RR=3 (22.7% vs 51.5%).
- SETUP2_BEAR_BREAKOUT (n=7, too thin to weight much): expectancy -0.51R,
  FAILS at RR=2, marginally PASSES at RR=3 (42.9% actual vs 38.3% required,
  +4.6% margin) -- not treated as a finding given n=7.
- Setup3/Setup4 (reversal): **zero trades**, confirmed again even with the
  fix in place -- this was never a volume-bug artifact for these two,
  `delta_flip_ok`'s near-total absence is a real structural finding, not a
  data-quality one.
- Walk-forward: **10 of 11 folds have literally zero trades**; the one fold
  that fired (test window 2025-10-31..2026-02-01, n=10) lost -0.56R. This is
  a starker picture than the contaminated run's "1/30 folds positive" --
  once fake signal is removed, the real setup barely fires at all, and on
  the one occasion with enough of a sample to judge, it lost.

**Acceptance check (re-run against corrected numbers,
`engine.walkforward.champion_vs_challenger(0.0, -1.2927, folds, cfg)`):**
`adopted=False` -- `oos_expectancy_drop_pct=100.0` (champion v1's OOS
expectancy is 0, challenger's is negative), `positive_folds=0` of 11.
**REJECTED**, same verdict as the contaminated run, now on trustworthy data.

**Interpretation:** the corrected result is actually a cleaner story than
the contaminated one. Genuine order-flow confirmation for this rulebook,
once the fake zero-volume signal is stripped out, is rare enough on NIFTY
5-min (n=29 over 4.5 years) that most walk-forward windows never even get a
trade -- and the one window with a real sample lost money. Combined with
the cost-adjusted breakeven failure and v1's original near-zero-trade
result under the strict-AND rule, three independent looseness levels
(strict AND / score-3-of-4 / score-3-of-4 minus fake volume signal) all
point the same way: this isn't a sampling problem to fix with more data or
looser thresholds, it's an absence of real edge at this timeframe/cost
structure. **Champion remains v1.**

**Status:** REJECTED. CHANGELOG entry corrected 2026-09-29 after the
zero-volume bug fix; the pre-fix draft (n=363, -0.97R) is superseded by the
numbers above and should not be cited.

**Addendum: `reports/funnel_diagnostic.json` regenerated (2026-09-29, via
the new `metrics/run_funnel_diagnostic.py`).** The specific fire-rate
percentages cited above and earlier in this entry's "Change" section
(CVD-new-high 6.3-9.4%, absorption 9.9-13.8%, delta/vol ~89-96%) came from
the PRE-FIX funnel run, over the pre-fix 2017-2026 date range -- both since
superseded. The regenerated diagnostic (post-fix, 2022-04-09 to
2026-09-28) shows every condition is far rarer than the original numbers
suggested: for SETUP1_BULL_BREAKOUT/5m, delta_ok fires on only **4.5%** of
trigger bars (not ~92% -- that figure was itself a zero-volume-bug
artifact), cvd_ok 11.3%, vol_ok 8.4%, absorption_ok **0.12%** (now the
clear bottleneck, not CVD). This doesn't change any verdict already
reached -- it reinforces the same conclusion from a cleaner number: genuine,
non-degenerate order-flow confirmation is rare on this dataset across every
condition, not just one, consistent with the corrected backtest's thin,
loss-making sample. The specific per-condition fire-rate numbers quoted
earlier in this file's "Change" section are historical (the V-a rationale
at the time), not a current, trustworthy diagnostic -- use the regenerated
`funnel_diagnostic.json` for anything going forward.

## v4 -- 2026-09-29 (CHALLENGER, NOT ADOPTED -- SM-gate A/B ablation)

**Change:** Smart-Money Context gate (`require_sm_alignment: true`) applied
to setup1/setup2 on top of v3 (setup3/4 left ungated -- 0 trades regardless,
per the funnel diagnostic). Real comparison is **v3 (A, ungated) vs v4 (B,
SM-gated)**, not v1 vs v4 -- see `config/v4.yaml`'s own header for why (v1
has ~0 trades; gating an empty baseline is uninformative).

**Backtest result:** `reports/run_v4_nifty_5m/`. n=24 in-sample (down from
v3's n=29), n=13 OOS (down from v3's n=22) -- both still
INSUFFICIENT_SAMPLE per G1 (<30), and B's OOS sample **shrank further**
below A's, which was already sub-threshold.
- Global: expectancy -1.19R (IS, worse than A's -1.08R), **-0.90R (OOS,
  better than A's -1.29R but still solidly loss-making)**, PF_net 0.196
  (IS), PF_net 0.347 (OOS).
- SETUP1_BULL_BREAKOUT (n=19): expectancy -1.36R, FAILS cost-adjusted
  breakeven at both RR=2 and RR=3 (21.1% actual vs 70.2%/52.6% required).
- SETUP2_BEAR_BREAKOUT (n=5, too thin to weight): expectancy -0.54R.
- Walk-forward: same pattern as v3 -- **10 of 11 folds have zero trades**;
  the one fold with a sample (test 2025-10-31..2026-02-01) lost -0.86R,
  *worse* than v3's -0.56R on the identical fold.
- Setup3/Setup4: still zero trades (unaffected by this gate by design).

**G8 acceptance check** (`adopted` only if B beats A on OOS expectancy
*without* collapsing trade count below G1's 30-trade minimum): B's OOS
expectancy is numerically less negative (-0.90 vs -1.29), **but** A's OOS
count (22) was already below the 30-trade minimum, and B's count (13)
collapses it further -- the sample-collapse clause fails outright regardless
of the expectancy direction. **NOT ADOPTED.**

**"C: SM-only" scope decision (disclosed):** the spec's third ablation arm
asks whether the SM context alone, without any setup1-4 trigger, generates
a tradeable signal. Given (a) this single v4 run took 158 minutes of real
wall-clock time on this host (the SM stack's per-bar cost, mainly Wyckoff's
O(range_lookback) range scan, run across ~4.5 years of 5-min bars for 2
setups), (b) B already under-performs A on IS expectancy and on the one
walk-forward fold with a real sample, and (c) both existing arms are
n<30/INSUFFICIENT_SAMPLE with a consistently negative sign -- building a
third, fully separate SM-only backtest engine (a new entry/exit/stop
convention with no setup gate at all) was judged disproportionate to the
marginal information it would add: there is no reason from A or B's results
to expect a same-inputs, less-constrained signal would clear costs where a
more-constrained one didn't. This is a reasoned scope call, not an empirical
result -- flagged explicitly rather than fabricated. If revisited, "C"
should be built once the host isn't compute-constrained, or as a much
cheaper offline check (e.g. bias_score's own direction accuracy over the
next N bars, without a full trade simulation).

**SM-5 (Order Block) scope note (disclosed, unchanged from Arc F):** still
deferred, not built. The 8 implemented detectors (SM-1/2/3/4/6/7/8/9) were
judged sufficient to answer the gate/no-gate question this ablation was
built for; SM-5 would need its own separate justification to add later.

**Interpretation:** consistent with every other version tested (v1: too few
trades to judge; v2: rejected; v3: rejected, decisively once the volume bug
was fixed). Restricting to SM-aligned setups filters the sample further
without turning the underlying economics positive -- the setup's edge
problem (real per-trade cost exceeds the R-unit available at this timeframe)
isn't a confirmation-quality problem that a smarter gate can fix. **Champion
remains v1.**

**Status:** NOT ADOPTED (G8 fails on sample-collapse). Task "run A/B/C
ablation through gates" is now closed: A vs B ran to a decisive verdict on
real, bug-fixed data; C was scoped down to a disclosed non-empirical
judgment call rather than a fourth multi-hour run on a resource-constrained
host, for the reasons above.
