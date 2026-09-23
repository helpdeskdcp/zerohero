# Hedging Engine Phase 1 + 2a — Real-Chain Integration Validation

Not a statistical backtest -- this session already established that real
option-chain snapshot density (~2-3 snapshots/strike/day for most
instruments) is too sparse to run a genuine historical backtest of a
chain-dependent selection engine (see
`data/research/reverse_engineering/PHASE0_PHASE1_REPORT.md`). What this
*is*: a real end-to-end run of the whole Phase 1+2a pipeline
(`select_hedge` → `open_position` → `check_exit` → `close_position`)
against the actual live-captured NIFTY chain (`app.optionchain.resolve
.get_chain`), proving the pieces integrate correctly and the math is right
on real numbers, not just synthetic unit-test fixtures.

## Run 1 — default ₹50,000 capital (real chain, 2026-09-23 07:xx IST snapshot)

Primary: SELL NIFTY 23300 CE @ 28.85 (real premium, real delta 0.9923).

`select_hedge` evaluated 18 real further-OTM strikes, 2 cleared every
gate (liquidity/spread/cost-vs-credit). Cheapest accepted candidate:
24150 CE @ 0.45 -- but 850 points away, giving `max_loss_per_lot` =
₹53,404. At the default 2% risk cap, ₹50,000 capital allows a ₹1,000
risk budget -- **0 lots fit, correctly NO_TRADE.**

This is the intended behavior, not a bug: the spec's own rule is
"Position quantity must be calculated from available capital and maximum
permitted risk... NO TRADE is valid when no safe structure exists." A
₹50,000 paper account genuinely cannot safely run this specific
structure at the moment this chain was captured.

## Run 2 — ₹10,000,000 capital (demo-only, to complete one full lifecycle)

Same primary/hedge selection (identical real numbers) now sizes to 3
lots (risk budget ₹200,000 / ₹53,404 per lot). Full lifecycle:

```
OPENED:  lots=3, margin_locked=₹160,212, cost_paid=₹87.75
capital after open:  available=₹9,839,700.25, allocated_margin=₹160,212
```

Simulated the primary premium decaying 60% (28.85 → 11.54) mid-session:

```
check_exit -> PROFIT_TARGET, pnl=₹3,375.45
CLOSED. capital after close: available=₹10,003,287.70, allocated_margin=₹0, realized_pnl=₹3,375.45
```

Arithmetic check (by hand): `9,839,700.25 + 160,212 (margin released) +
3,375.45 (pnl) = 10,003,287.70` -- matches exactly. The capital ledger's
allocate/release round-trip is correct on a real, non-synthetic scenario.

## What this validates

- `select_hedge` correctly reads a real `OptionChain` (98.7% Greek
  coverage, 43.6% OI/LTP coverage on this snapshot -- the real, known
  sparsity), correctly filters to only further-OTM same-type strikes,
  correctly rejects on real liquidity/spread/cost gates.
- Capital-based sizing correctly returns 0 lots (real NO_TRADE) when
  the real economics don't fit real capital -- proven on two different
  capital levels against the identical real trade.
- The open → check_exit → close lifecycle correctly moves money through
  margin lock/release and books realized P&L with no arithmetic drift.

## What this does NOT validate

- Whether this structure/threshold set is profitable over time -- no
  real historical chain density exists to answer that (same limitation
  documented for every option-chain-dependent research this session).
- Live execution, Telegram, frontend, or continuous monitoring -- none
  of those exist yet (Phase 2a is position/exit-rule bookkeeping only).
