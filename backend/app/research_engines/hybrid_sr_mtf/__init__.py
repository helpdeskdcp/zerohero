"""Hybrid support/resistance multi-timeframe research engine (RESEARCH ONLY: no order path, no production import).
15m bias + major zones -> 5m structure event (liquidity sweep / break-and-retest / trend pullback) -> 1m micro-break trigger, with a strict NO-TRADE filter list.
Everything is causal: a decision at time t uses only bars that COMPLETED at or before t, and the entry is the OPEN of the next 1-minute bar."""
