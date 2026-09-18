# Order-Flow Stage-10 — The Absorption Detector (§3.9), Built and Tested

**Status:** RESEARCH ONLY. No production code changed. No signal, no orders,
no live wiring. Same discipline as every prior orderflow stage.

**Task:** the ORDERFLOW_ENGINE_V2_SPEC.md §3.9 "Absorption" concept was
written but never actually built or backtested ("buildable (weak)"). This
was the one candidate hypothesis this session identified as genuinely
unexplored — H7_TRAP fade was already tested and REJECTED
(ORDERFLOW_STAGE8_H7_FADE.md), and an adjacent MTF-confirmation mechanic was
already NOT VALIDATED (IMBALANCE_NEXT_CANDLE_1R3_MTF_OI.md).

Script: `backend/scripts/orderflow_absorption_research.py` →
`data/research/orderflow/absorption_backtest.json`.

## Data

Real captured L2 SnapQuote data (`data/l2_capture.db`, `snapquote_ticks`) —
best-5 depth + `tot_buy_qty`/`tot_sell_qty` aggregate order totals per
snapshot, resampled to real 5m bars. **7-8 real trading days per symbol
(2026-09-09 → 2026-09-18)** — far thinner than any other backtest this
session. `delta_snap_bar` = the change in `(tot_buy_qty - tot_sell_qty)`
across a bar — a PROXY for directional pressure from resting order-book
totals, **not genuine per-trade aggressor data** (that data type does not
exist anywhere in this system, confirmed repeatedly across prior stages).

## Result: the pattern essentially never fires, and the mechanism is now understood

Built the exact §3.9 conditions (S1-S5) as specified. At the spec's own
stated thresholds (`α=0.25, β=0.6, γ=0.35, κ=2`): **zero events on all
three symbols.** A full sensitivity sweep (`β` up to 3.0, `α` up to 0.75 —
5x the spec's stated values) found:

| symbol | S1 (volume) | S2 (price contained) | S3 (delta/volume ≥ γ) | S4 (band touch) |
|---|---|---|---|---|
| NATURALGAS | 239 / 1220 windows | 32 | **2** | 2 |
| CRUDEOIL | 223 / 1221 windows | 24 | **0** | 0 |
| NIFTY | 94 / 495 windows | 18 | **1** | 1 |

Volume and price-containment (S1, S2) pass at a normal few-percent rate once
`β` is relaxed to a realistic multi-bar-window scale (the spec's stated
`β=0.6` assumes a tighter window/bar relationship than a 4-bar/5m window
actually produces on this real data — literally zero windows pass at the
spec's own default). **The real bottleneck is S3**: the delta/volume
pressure ratio almost never reaches 0.35, on any symbol, at any of the
relaxed price-containment settings tested.

**Why**: `tot_buy_qty`/`tot_sell_qty` are cumulative resting order-book
totals, not executed trade flow — exactly the weakness the original spec
already flagged ("weakened by sampling and by the absence of a per-print
sequence"). This proxy is too noisy/weak to ever show a clean, sustained
directional signal strong enough to clear a 35%-of-volume threshold.

## Verdict

**UNTESTABLE with current data, not PROVEN or REJECTED.** Unlike H7_TRAP
fade (which had a real sample and a decisively negative result), Absorption
never generates enough qualifying events (1-2 per symbol, even after a 5x
threshold relaxation) to say anything about win rate or expectancy — the
sample is too thin to be meaningful in either direction. This is a data-type
limitation, not a implementation bug: `delta_snap_bar` from resting
order-book totals is fundamentally the wrong signal for this pattern.
Genuine per-trade aggressor/tick data would be required to test §3.9 as
originally intended — that data type does not exist in this system.

## What would actually let this be tested

- Real aggressor-classified tick data (does not exist here) -- OR
- A materially longer L2 capture history (7-8 days is a first pass; months
  would be needed even with a weaker proxy) -- OR
- A redefinition of the pressure signal using something this system CAN
  observe cleanly (e.g., is there a real signal in resting-size *changes*
  at specific price levels, rather than the aggregate buy/sell total?) --
  untested, a genuinely different next hypothesis, not this one.

No production code touched. No signal, no orders, no live wiring.
