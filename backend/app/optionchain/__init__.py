"""
IDaddy Option-Chain DATA LAYER  ::  READ-ONLY research module.

One canonical `OptionChain` shape assembled from up to three sources, in order:

  1. angelone_captured  -- PRIMARY. Built from data IDaddy already captures:
       market_history.db `quote_snapshots` (OPTION: ltp/bid/ask/oi/oi_change/
       volume) joined with `option_greeks` (delta/gamma/theta/vega/iv). No
       network -- reads the local capture DB read-only.
  2. upstox_open        -- SECONDARY. The keyless Upstox
       `option-analytics-tool/open/v1/strategy-chains` endpoint -> per-strike
       CE/PE with pre-computed Greeks + IV. NIFTY / BANKNIFTY only.
  3. nse_v3             -- TERTIARY. NSE `api/option-chain-v3` (cookie-primed).
       OI / change-OI / IV / volume, NO Greeks. NSE indices only (no SENSEX).

`resolve.get_chain()` tries them in order and, if the winning source lacks
Greeks, merges Greeks from a later source onto its rows.

Audit + plan: backend/OPTIONCHAIN_GITHUB_AUDIT.md . CE/PE-pairing concept
adapted from markov404/AngelOneOptionChainSmartApi (MIT). All other repos in the
audit have no licence -> only public facts (endpoint URLs, headers, JSON field
names) were used; no code was copied.

SAFETY: no broker order path, no `live_trading`, no API keys (Upstox + NSE are
keyless; Angel uses the existing capture DB). Imports nothing from
app.autoscalp / execution / main. This layer only PRODUCES a chain object --
analytics / structure / ANN / qualification / dashboard are separate later
layers and are NOT built here.
"""
from .chain import OptionChain, StrikeRow, OptionLeg          # noqa: F401
from .resolve import get_chain                                # noqa: F401
