"""
Institutional Edge layer -- section 12 (Layer 1) of the "ZEROHERO -- FINAL
PRODUCTION ARCHITECTURE..." mega-brief.

An independent ANALYTICAL layer, same posture as app.structural_break:
read-only over already-captured data, dark by default, never wired into
live signal generation or order execution. It assesses whether an existing
raw signal has measurable historical edge, current-market confirmation, and
positive expected value net of realistic costs -- it does not generate
trade signals of its own.

  costs.py            -- itemized, honestly-scoped realistic cost model
  conditional_edge.py -- P(outcome|condition) - P(outcome|baseline)
  ev.py               -- Gross / Net / Risk-adjusted EV
  edge_score.py        -- explainable score + CANDIDATE..INVALID state machine
  store.py            -- persistence, own dedicated DB file
  api.py              -- read-only routes
"""
