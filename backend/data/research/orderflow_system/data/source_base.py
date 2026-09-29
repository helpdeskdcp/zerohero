"""Abstract DataSource interface (spec Section 2). Every mode produces a
DataFrame with a minimum common schema (OHLCV) plus order-flow columns, and
self-reports whether those order-flow columns are REAL (tick-derived) or
PROXY (OHLCV-approximated) via `is_proxy`. validate() fails loudly rather
than letting a strategy silently run against incomplete data."""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

REQUIRED_OHLCV = ("open", "high", "low", "close", "volume")
REQUIRED_ORDERFLOW = ("delta", "cvd", "is_absorption", "is_imbalance")


def pandas_freq(tf: str) -> str:
    """Config timeframes are written "5m"/"15m"/"1m" (readable, matches the
    rulebook's own vocabulary); pandas >= 2.2 deprecated bare "m" as a
    minute alias (it now means month-end) -- translate to "min" here, in
    exactly one place, rather than writing "5min" in every config."""
    return tf[:-1] + "min" if tf.endswith("m") and not tf.endswith("min") else tf


class DataSource(ABC):
    mode: str = "abstract"
    is_proxy: bool = True  # False only for genuine tick-derived (Mode A/B) data

    @abstractmethod
    def load(self, *, symbol: str, kind: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        """Returns a DataFrame indexed by bar-open datetime (Asia/Kolkata),
        columns at minimum REQUIRED_OHLCV + REQUIRED_ORDERFLOW."""
        raise NotImplementedError

    def validate(self, df: pd.DataFrame) -> None:
        missing = [c for c in REQUIRED_OHLCV + REQUIRED_ORDERFLOW if c not in df.columns]
        if missing:
            raise ValueError(f"{self.mode}: DataSource output missing required columns {missing} "
                            f"-- refusing to silently proceed with incomplete order-flow data")
        if df.empty:
            raise ValueError(f"{self.mode}: DataSource returned zero rows for the requested range")

    def warning_banner(self) -> str | None:
        """None for genuine (non-proxy) data; a loud disclosure string for
        proxy modes -- printed at the top of every report (spec C4)."""
        return None
