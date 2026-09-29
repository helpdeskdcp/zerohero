# orderflow_system

A reproducible, backtestable, versioned implementation of a manual
order-flow rulebook (4 setups: bullish/bearish breakout, bullish/bearish
reversal), built against this repo's real captured/backfilled NIFTY data.

## Data reality -- read this before trusting any number

**True order flow requires tick data with bid/ask (or trade-level aggressor
side). OHLCV bars cannot contain true delta.** This system implements three
data modes (`data/source_*.py`) and states up front which one produced any
given result:

- **`tick` (Mode A)** -- real aggressor classification from Dhan's live
  WebSocket Full packets (5-level market depth), persisted by
  `app/engines/orderflow_store.py` starting **2026-09-29**. As of this
  writing there are only a few hours of real ticks captured -- nowhere near
  enough for a real backtest. This mode activates automatically, with no
  code change, once enough history has accumulated (`min_ticks` in
  `data/source_tick.py`); until then it raises rather than silently
  returning too little data to trust.
- **`broker_api` (Mode B)** -- not currently wired; Dhan's real-time feed IS
  the tick source above, there is no separate lower-fidelity broker-API path
  in this codebase to fall back to.
- **`ohlcv_proxy` (Mode C)** -- the ONLY mode with enough sample size right
  now: 5 years of real NIFTY/BANKNIFTY/SENSEX 1-minute bars
  (`market_history.db::market_candles`, `source=DHAN_HISTORICAL`), resampled
  to the target timeframe. Delta/CVD/absorption/imbalance are all
  **approximated** from price/volume shape -- exact formulas are in
  `data/source_ohlcv_proxy.py`'s docstring and `config/base.yaml`'s
  `proxy:` section. **Every report produced in this mode is prefixed with a
  loud `OHLCV PROXY -- APPROXIMATION, NOT TRUE ORDER FLOW` warning** and
  this is not optional.

**NSE-specific limitation, stated explicitly per this project's own
instructions:** true bid/ask order flow for NSE cash equity is generally
not available to retail traders; footprint/delta tools in practice use
futures or proxy data, often delayed. This system's index-level data has
the same character. Prefer the 5m-15m timeframes this project defaults to
over scalping -- delayed/proxy data makes scalp-timeframe order-flow claims
invalid, which is exactly why `config/base.yaml` only exposes `5m`/`15m`/
swing timeframes.

## Prior evidence in this repository

Before this project existed, this same codebase tested several other
order-flow-adjacent hypotheses (L2 imbalance, tick-rule CVD-vs-SMA
crossover, SSL Hybrid, liquidity-sweep SMC) against real NIFTY/BANKNIFTY
data -- all closed **NO-GO**. That history doesn't predict this rulebook's
outcome, but it sets a realistic prior: don't be surprised if setup1-4 also
show no edge. The point of this project is a systematic, auditable way to
find that out (or not), not a guarantee either way.

## Running it

```bash
cd data/research/orderflow_system
python main.py backtest --config config/v1.yaml          # official event-driven run
python main.py walkforward --config config/v1.yaml       # rolling train/test + OOS holdout
python main.py sensitivity --config config/v1.yaml        # vectorized screening pass ONLY
```

Every run dumps its full resolved config, the git commit hash, the data
version (symbol/source/date range), and `backtest.seed` into
`reports/run_<timestamp>/manifest.json`, per the reproducibility
requirement -- so any number can be traced back to exactly what produced it.

## Disclaimer

This system is for **research and education only**. Backtested results are
historical and do NOT guarantee future performance. Order-flow data on NSE
for retail traders is often delayed or proxy-based; OHLCV proxy mode
produces **approximations**, not true order flow. All performance figures
are net of modelled costs, but real slippage may differ. This is **not
investment advice**. Consult a SEBI-registered advisor before trading real
capital. Risk is the user's own.
