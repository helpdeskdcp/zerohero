# pa_of_lab — Price Action + Order Flow research lab (RESEARCH ONLY, no order path)

Page: `/static/pa_of_lab.html` (needs the `oi-dashboard` restart to mount `/api/pa_of_lab/*`; **not deployed automatically**). Kill switch: `PA_OF_LAB=0`.

| Module | Role |
|---|---|
| `schema.py` | canonical bar, provenance classes, `footprint_to_canonical` / `to_orderflow_input` adapters, data-quality rules |
| `ticks.py` | read-only loader over `data/dhan_orderflow_ticks.db`; dedupe / late / stale / gap policies; bar builder using the live `FootprintBar` |
| `pa_events.py` | confirmed + provisional pivots, BOS/CHoCH, EQH/EQL, sweeps, rejections, retests, levels, VWAP (only with real volume), signals + simulated outcome |
| `orderflow.py`, `price_action.py` | the two signal engines (unchanged rules) |
| `confirm.py` | PRICE ACTION CONFIRMED / ORDER FLOW CONFIRMED / BOTH CONFIRMED / NO TRADE; order flow counts only on data-quality-OK bars |
| `api.py`, `log.py` | `/api/pa_of_lab/{meta,chart}`; research log of every signal shown |

Real vs inferred: best bid/ask = observed quote; buy/sell/delta/CVD/imbalance = INFERRED from quote/tick rule (never broker-confirmed); candle source = no footprint at all.
Footprint exists only for tick-covered (symbol, date) pairs from 2026-09-29 onward; there is no historical aggressor/bid-ask data to replay older sessions.
Baseline (unchanged, `scripts/pa_lab_backtest.py`): NIFTY 5m 2021-26, 711 trades, gross +0.31 / net -5.99 bps per trade at 6.3 bps cost, 33.9% win. Charts and order flow do not change that result.
