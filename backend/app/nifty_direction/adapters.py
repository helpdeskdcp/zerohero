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
