"""
Microstructure / Operator-Behaviour Proxy layer -- section 13 (Layer 2) of
the "ZEROHERO -- FINAL PRODUCTION ARCHITECTURE..." mega-brief.

Never claims direct knowledge of operator identity or intent (per the
brief's own opening line for this layer) -- it is a PROXY read over price
action + volume + option-chain structure, explicitly labelled as such.

Deliberately a TRANSLATOR, not a new detector: app.orderflow.h1h7_state
already runs a real, deterministic, causal price-action state machine
(spike/level-break/reclaim/acceptance classification -- see its own module
for the H1/H7 research history) over captured OHLCV bars. Rebuilding
absorption/rejection/breakout detection from scratch here would duplicate
that machinery for no reason. `state.py` maps h1h7_state's existing output
onto this layer's own required vocabulary (BUYING_PRESSURE / SELLING_
PRESSURE / ABSORPTION / REJECTION / BREAKOUT / FAILED_BREAKOUT / BALANCED /
UNKNOWN), with an explicit, named reason for every mapping decision.

  state.py     -- pure translator: one h1h7 event -> one MicrostructureState
  evaluator.py -- thin wrapper over orderflow.service.h1h7_state (real bars)
  api.py       -- read-only route
"""
