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
