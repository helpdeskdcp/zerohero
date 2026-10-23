"""Provider-independent historical-data interface. Adapters only fetch and normalise; strategy code never imports a broker SDK.
Existing broker integrations in this repo (app/dhan_client.py, app/connectors/upstox.py, the Angel One feed) are NOT called from here: wrap them in a Provider subclass when needed. No network access happens in this package by default."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

import pandas as pd

from ..loader import load_csv, load_dataframe
from ..quality import validate_one_minute


class ProviderError(RuntimeError):
    pass


class HistoricalProvider(ABC):
    name = "base"

    @abstractmethod
    def fetch_1m(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """Return RAW normalised 1-minute rows (see data.schema)."""


class CsvProvider(HistoricalProvider):
    name = "csv"

    def __init__(self, path: str, **kw): self.path, self.kw = path, kw

    def fetch_1m(self, symbol, start, end):
        d = load_csv(self.path, **self.kw)
        return d[(d.index >= pd.Timestamp(start, tz="Asia/Kolkata")) & (d.index < pd.Timestamp(end, tz="Asia/Kolkata"))]


def with_backoff(fn, *, retries: int = 3, base_sec: float = 0.5, cap_sec: float = 8.0, sleep=time.sleep):
    """Bounded exponential back-off. Re-raises the LAST error; never fabricates a success."""
    err = None
    for k in range(retries + 1):
        try: return fn()
        except Exception as e:  # noqa: BLE001
            err = e
            if k < retries: sleep(min(cap_sec, base_sec * (2 ** k)))
    raise ProviderError(f"failed after {retries + 1} attempts: {type(err).__name__}: {err}") from err


def fetch_with_fallback(providers: list[HistoricalProvider], symbol: str, start: str, end: str, *, min_rows: int = 1, sleep=time.sleep) -> tuple[pd.DataFrame, dict]:
    """First provider whose data is non-empty AND passes validation wins; every attempt is recorded. If none does -> ProviderError (no invented data)."""
    health = []
    for p in providers:
        try:
            raw = with_backoff(lambda: p.fetch_1m(symbol, start, end), sleep=sleep)
            clean, rep = validate_one_minute(raw)
            if len(clean) < min_rows: health.append({"provider": p.name, "ok": False, "why": f"only {len(clean)} valid rows"}); continue
            clean = clean.assign(source=p.name); health.append({"provider": p.name, "ok": True, "rows": int(len(clean))})
            return clean, {"used": p.name, "health": health}
        except ProviderError as e:
            health.append({"provider": p.name, "ok": False, "why": str(e)})
    raise ProviderError(f"no provider returned valid data: {health}")
