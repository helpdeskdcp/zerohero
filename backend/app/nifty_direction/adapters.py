"""Broker-agnostic market-data adapter contract + registry + deterministic mock. Data only; by design there is no order method."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Protocol


@dataclass(frozen=True)
class Quote:
    symbol: str
    ltp: float
    prev_close: float | None
    ts: float                     # exchange/receive epoch seconds
    volume: float | None = None
    source: str = ""


class MarketDataAdapter(Protocol):
    name: str
    def quotes(self, symbols: list[str]) -> dict[str, Quote]: ...
    def candles(self, symbol: str, interval: str, count: int) -> list[dict]: ...      # [{t,o,h,l,c,v}] oldest first, completed bars only
    def health(self) -> dict: ...


_REG: dict[str, Callable[[], MarketDataAdapter]] = {}


def register(name: str, factory: Callable[[], MarketDataAdapter]) -> None:
    _REG[name] = factory


def get(name: str) -> MarketDataAdapter:
    if name not in _REG:
        raise KeyError(f"unknown adapter {name!r}; registered: {sorted(_REG)}")
    return _REG[name]()


def names() -> list[str]:
    return sorted(_REG)


@dataclass
class MockAdapter:
    """Test double: caller supplies quotes/candles; never fabricates data on its own."""
    name: str = "mock"
    _q: dict = field(default_factory=dict)
    _c: dict = field(default_factory=dict)
    clock: Callable[[], float] = time.time

    def set_quote(self, symbol, ltp, prev_close=None, ts=None, volume=None):
        self._q[symbol] = Quote(symbol, ltp, prev_close, self.clock() if ts is None else ts, volume, self.name)

    def set_candles(self, symbol, interval, rows): self._c[(symbol, interval)] = list(rows)

    def quotes(self, symbols): return {s: self._q[s] for s in symbols if s in self._q}

    def candles(self, symbol, interval, count): return self._c.get((symbol, interval), [])[-count:]

    def health(self): return {"adapter": self.name, "ok": True, "quotes": len(self._q)}


register("mock", MockAdapter)


# ---- optional capabilities (futures / option chain). An adapter advertises what it really has; callers never assume. ----
CAP_QUOTES, CAP_CANDLES, CAP_FUTURES, CAP_CHAIN = "quotes", "candles", "futures", "option_chain"


def capabilities(adapter) -> set[str]:
    fn = getattr(adapter, "capabilities", None)
    return set(fn()) if callable(fn) else {CAP_QUOTES, CAP_CANDLES}


def _mock_caps(self):
    c = {CAP_QUOTES, CAP_CANDLES}
    if getattr(self, "_fut", None) is not None: c.add(CAP_FUTURES)
    if getattr(self, "_chain", None) is not None: c.add(CAP_CHAIN)
    return c


def _mock_set_futures(self, d): self._fut = d                   # dict: symbol, expiry, ltp, prev_close, volume, oi, prev_oi, ts
def _mock_set_chain(self, d): self._chain = d                   # dict: underlying, expiry, ts, spot, rows=[{strike,ce_oi,pe_oi,ce_vol,pe_vol,ce_iv,pe_iv,ce_delta,pe_delta}]
def _mock_futures(self): return self._fut
def _mock_chain(self): return self._chain


MockAdapter.capabilities = _mock_caps
MockAdapter.set_futures = _mock_set_futures
MockAdapter.set_chain = _mock_set_chain
MockAdapter.futures = _mock_futures
MockAdapter.option_chain = _mock_chain
MockAdapter.data_kind = "MOCK"
