"""
Autonomous Intraday Hedging Engine -- Phase 1 (capital ledger + hedge
selection math only).

Scope of this phase, explicitly: deterministic hedge-selection decision
logic and a PAPER capital ledger. No Telegram, no frontend, no live
wiring, no broker order of any kind -- this package never imports
app.execution's broker adapters. Reuses, never modifies, the existing
option-chain (app.optionchain.chain.OptionChain/OptionLeg), Greeks, and
instrument-profile (app.instrument_profiles) infrastructure.

Structure covered in Phase 1: hedging a SOLD (short) option leg with a
BOUGHT option of the SAME type at a further OTM strike -- a same-type
vertical credit spread. This is the one hedge structure whose economics
(max loss, net credit, margin-as-max-loss) are exactly computable without
a broker margin-calculator API or an invented formula. Other structures
(cross-type collars, ratio spreads, ...) are out of scope for this phase,
not silently approximated.
"""
