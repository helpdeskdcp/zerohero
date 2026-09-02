"""
Option Greeks Engine — NIFTY.

A dedicated, read-only compute layer over the histcap capture store. It NEVER
fetches or fabricates a Greek: it consumes the broker Greeks + OI that
`app/histcap` already records from AngelOne `marketData/v1/optionGreek` +
`market/v1/quote`, then derives exposure aggregates and persists them
**append-only** to their own tables (kept strictly separate from the raw
`option_greeks` capture).

What it derives (only from valid captured data):
  per-strike  Δ/Γ/Θ/V exposure = OI × Greek
  CE / PE totals + net + (CE − PE) difference per Greek
  OI-weighted IV, Vega-weighted IV
  Greek-weighted OI concentration (gamma) — dominant strike, share, Herfindahl
  PCR(OI)

Data-quality on every derived snapshot: VALID | STALE | PARTIAL | INVALID | NO_DATA.
Reuses: histcap `HistStore` (DB + captured rows), `broker.angelone.greeks`
normalisation, `market_calendar`, the histcap scheduler (the worker calls
`GreeksEngine.run_once` once per cycle). No order path, no trading-logic change.
"""
from .engine import GreeksEngine
from .model import Quality

__all__ = ["GreeksEngine", "Quality"]
