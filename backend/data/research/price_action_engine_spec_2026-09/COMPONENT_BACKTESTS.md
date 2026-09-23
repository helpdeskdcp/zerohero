# Component Backtests — 4 New Price-Action Pieces (separate verdicts)

Real data only, read-only reuse of `app/liquidity_sweep/structure.py` and
`resample.py` (unmodified — verify via `git diff --stat -- app/liquidity_sweep
app/strategy_mtf`, empty). No integration into any live/paper path. Script:
`data/research/price_action_engine_spec_2026-09/component_backtests.py`,
raw output: `raw_component_results.json`.

## 1. MTF Cascade (30m→15m→5m; 1m/3m DATA_BLOCKED)

**1m/3m cannot be tested honestly**: the real 2-year NIFTY dataset
(`load_kaggle_nifty_bars`) is 5m-only (already documented in
`liquidity_sweep/backtest.py`'s own module docstring) — building 1m/3m from
5m bars would fabricate data. Tested the 30m/15m/5m sub-cascade only.

- **Method**: `htf_bias()` (existing, unmodified) on 5m/15m/30m separately;
  a bar counts as a full-cascade setup only when all 3 agree BULLISH or all
  3 agree BEARISH. Forward 1h (12×5m bars) return checked against the
  agreed direction.
- **n = 266** aligned setups (2 years real data).
- **Favorable-direction hit rate: 49.6%** overall. First half 45.9%, second
  half 53.4% — no consistent edge, moves ~7.5pp on chronological split alone.
- **Verdict: NO-GO.** Coin-flip, doesn't survive its own split.

## 2a. Daily Floor-Pivot Side (pivot only, no volume needed) — 2yr real data

- **Method**: real prior-day OHLC → `PP=(H+L+C)/3`; forward 1h return
  compared for `close > PP` (bullish side) vs `close < PP` (bearish side).
- **n = 23,071 bull-side / 20,735 bear-side** (large, real, full 2yr).
- Bull side: +1.43 (1st half) → +2.97 (2nd half), consistently positive.
  Bear side: -0.51 → -1.90, consistently negative.
- **Verdict: PROMISING, with a real caveat.** Direction is consistent across
  the split, but NIFTY rose ~21,700→23,650 over this exact 2-year window —
  "price above the daily pivot" substantially overlaps with "market is in
  its dominant uptrend," so this may just be re-discovering the trend, not
  finding pivot-specific information. Would need de-trending (e.g. compare
  against the unconditional mean forward return over the same period,
  which this run did not compute) before calling it more than PROMISING.

## 2b. VWAP+Pivot AND-Gate (real volume required) — small real window

NIFTY the cash index has **zero real volume** (confirmed again in this
dataset — matches this session's established finding) — a true VWAP needs
real volume, so this was tested on the NIFTY FUTURE's real captured ticks
(`market_history.db`), which is a much smaller, more recent window.

- **n = 3,938 observations, 13 real trading sessions** (2026-09-02 to
  2026-09-21) — well below this session's own ~40-session minimum for a
  meaningful conclusion; this is a genuine data-availability ceiling, not a
  shortcut taken here.
- `AND_BULL` (price>VWAP and price>pivot): mean forward return **-0.464**
  (negative — opposite of what a "bullish" label should show).
  `AND_BEAR`: **-0.018**. `MIXED` (gate disagrees): **-2.742** (most
  negative of the three, also not the expected "no-signal → near-zero"
  pattern).
- **Verdict: NO-GO / INSUFFICIENT_DATA.** Sample too thin for a real verdict,
  and what data exists doesn't show the expected sign pattern.

## 3. Multi-Timeframe S/R Confluence

- **Method**: real 5m swing points (`swings()`, unmodified) vs real 30m
  swing points; a 5m swing counts as "confluence" when within 0.03% of a
  30m swing price, else "single-TF." Forward-1h favorable-reaction rate
  (bounce off a low / rejection at a high) compared between groups.
- **n = 11,721 confluence / 120 single-TF** (2 years real data) — note the
  single-TF group is thin, so that side of the comparison is weaker than
  the confluence side's own internal consistency.
- Confluence bounce rate: **60.4% → 61.1%** across the chronological split
  — stable. Single-TF: 52.9% → 54.6% (thinner sample, less reliable).
- **Verdict: PROMISING — the strongest result of the 4.** Large sample,
  consistent ~60% favorable-reaction rate that doesn't drift across the
  split. Not yet a trading edge: this measures directional bounce
  probability only, no P&L/cost/target-achievement test was run on it.
  Worth a real P&L-based follow-up before any GO call.

## 4. T1(2R)/T2(3R)/T3(4R) Ladder Hit Rates

- **Method**: real structural breakout setups (price closes beyond a
  confirmed swing high/low within 40 bars of confirmation, entry at that
  close, structural SL = the swing's own price — same philosophy as
  `liquidity_sweep/risk.py`'s sweep-extreme stop, not re-derived). Walked
  forward through real subsequent bars for SL-first-or-target-first.
- **n = 8,101 real setups** (2 years, large sample).
- **SL hit rate: 93.8%** before even reaching 2R. T1(2R): 25.9%, T2(3R):
  21.1%, T3(4R): 17.85% (measuring "ever touched," not mutually exclusive).
- **Verdict: NO-GO for the bare geometry.** A raw structural-breakout entry
  with no other confirmation gets stopped out 9 times in 10 before 2R. This
  measures the R:R ladder's OWN mechanics in isolation (as the user asked,
  separately, not integrated with any confirmation layer) — it says the
  ladder itself needs a much better-qualified entry to be worth using, not
  that 2R/3R/4R targets are wrong once a real edge exists upstream.

## Not done (out of scope for this pass, per user's exact request)

No combined/integrated verdict across the 4 components — that's explicitly
the next decision point, not run here. No de-trending control for component
2a. No P&L/expectancy test for component 3's bounce-rate finding.

`git diff --stat -- app/liquidity_sweep app/strategy_mtf` is empty — neither
package was touched.


## Component 3 — P&L Backtest (retry, capped sample)

Real bar-by-bar P&L walk for the multi-TF S/R confluence signal (5m swing
within 0.03% of a 30m swing price), reusing the exact confluence detector
from the hit-rate pass above. Not the T1/T2/T3 ladder (already NO-GO,
component 4) -- a single 1:1.5 R:R target for a first P&L read.

**Rule**: entry at the swing's confirmation bar close (i+2, since
`swings(left=2,right=2)` needs 2 forward bars to confirm a pivot -- no
look-ahead). Structural SL = swing price +/- 0.15% buffer
beyond the zone. Target = entry +/- 1.5R. Max hold 30 bars
(~2.5h), no overnight. Same-bar SL+target ambiguity resolved conservatively
(SL wins). GROSS-ONLY (NIFTY has no validated cost profile in
app.institutional_edge.costs, confirmed earlier this session -- not
fabricated here).

- Total confluence events found: 11721; sampled 500 (seed 42, cap 500); 500 simulated.
- **All trades**: {'n': 500, 'win_rate': 0.418, 'profit_factor': 0.984, 'expectancy_pts': -0.3278, 'avg_win': 47.29, 'avg_loss': -34.527, 'avg_mfe': 41.783, 'avg_mae': 38.336, 'max_drawdown_pts': -1062.047}
- **Forward split** (train=1st half, test=2nd half): {'n': 250, 'win_rate': 0.428, 'profit_factor': 1.017, 'expectancy_pts': 0.3389, 'avg_win': 48.505, 'avg_loss': -35.701, 'avg_mfe': 41.87, 'avg_mae': 40.877, 'max_drawdown_pts': -913.778}
- **Swapped split** (train=2nd half, test=1st half): {'n': 250, 'win_rate': 0.408, 'profit_factor': 0.95, 'expectancy_pts': -0.9945, 'avg_win': 46.015, 'avg_loss': -33.393, 'avg_mfe': 41.696, 'avg_mae': 35.795, 'max_drawdown_pts': -735.999}

**Verdict**: NO-GO --
edge does not survive both the forward and swapped split -- do not treat the earlier hit-rate result as validated for real P&L.
