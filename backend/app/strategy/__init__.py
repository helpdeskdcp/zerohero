"""
Strategy Verification Engine -- a SEPARATE, ADDITIVE layer sitting between
the existing signal-generation pipeline (app.engines.state_classifier +
app.engines.regime_mtf, already producing a raw BULLISH/BEARISH/NONE
direction from S/R + price-action + false-breakout logic) and any trade
execution path.

    MARKET DATA
        v
    Existing indicator/OI/volume/price-action engines (unchanged)
        v
    Existing signal generator (state_classifier.classify) -- unchanged
        v
    RAW SIGNAL (BUY | SELL | NONE)
        v
    THIS PACKAGE: Strategy Verification Engine
        v
    CE / PE / NO_TRADE (+ state: WATCH/SETUP/CONFIRMED/ENTRY_READY/INVALIDATED)
        v
    Risk filter / option contract selection (kept separate, see
    option_selection.py) -- out of scope for direction detection

Nothing in app/engines/ is modified by this package. Every existing
indicator this package needs (SMA, EMA, RSI, MACD, ADX, ATR, VWAP) is
imported READ-ONLY from app.engines.signal_engine's private helpers -- the
same reuse convention app.engines.regime_mtf and app.liquidity_sweep.
indicators already use elsewhere in this codebase. Bollinger Bands do not
exist anywhere in this codebase yet, so they are computed fresh here (not a
duplicate of anything).

PAPER / SIGNAL-ONLY: this package never places, modifies, or cancels a
broker order. Its only output is a verification result dict + state.

Modules:
  config.py        -- StrategyConfig: weights, thresholds, margins, all
                       overridable, none hard-coded into logic.
  base_strategy.py  -- MarketFeatures (causal input contract) and
                       StrategyResult (the exact per-strategy output
                       contract), plus the shared indicator snapshot.
  scoring.py        -- named condition checks (bullish + bearish) and the
                       weighted-bucket scorer; missing data degrades a
                       score, never crashes.
  ce_strategy.py    -- evaluate_ce(): bullish weighted strategy.
  pe_strategy.py    -- evaluate_pe(): bearish weighted strategy.
  conflict.py       -- CE-vs-PE decision + raw-signal-vs-strategy conflict
                       resolution (SIGNAL_CONFLICT -> NO_TRADE by default).
  confirmation.py   -- anti-whipsaw: persistence, spike-avoidance,
                       near-tie avoidance, noisy/sideways avoidance.
  state_machine.py  -- WATCH / SETUP / CONFIRMED / ENTRY_READY / INVALIDATED
                       / NO_TRADE, per symbol, across calls.
  strategy_verifier.py -- StrategyVerifier: the orchestrator, produces the
                       full audit dict the API/dashboard consume.
  option_selection.py -- ATM/ITM/OTM contract selection, deliberately kept
                       separate from directional logic (runs AFTER CE/PE
                       is already decided).
"""
