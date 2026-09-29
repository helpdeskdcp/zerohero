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

    def coverage_report(self, df: pd.DataFrame, timeframe: str) -> dict:
        """Bar counts, first/last date, and candidate gaps (weekdays with
        zero bars -- this WILL include real NSE holidays as false positives
        since this research project has no holiday calendar; disclosed here
        rather than silently filtered, since a silent filter could just as
        easily hide a genuine capture gap as a holiday)."""
        if df.empty:
            return {"n_bars": 0, "first": None, "last": None, "trading_days": 0,
                   "candidate_gap_weekdays": [], "is_proxy": self.is_proxy, "mode": self.mode}
        days_present = pd.Series(df.index.normalize().unique()).dt.date
        all_weekdays = pd.bdate_range(df.index.min().date(), df.index.max().date()).date
        gaps = sorted(set(all_weekdays) - set(days_present))
        nonzero_vol = df[df["volume"] > 0] if "volume" in df.columns else df.iloc[0:0]
        return {
            "n_bars": len(df), "timeframe": timeframe,
            "first": str(df.index.min()), "last": str(df.index.max()),
            "trading_days_with_data": len(days_present),
            "candidate_gap_weekdays": len(gaps),
            "candidate_gap_weekday_sample": [str(g) for g in gaps[:10]],
            "gap_note": "weekdays with zero bars -- includes real NSE holidays as false "
                       "positives (no holiday calendar in this research project); a large, "
                       "growing count over a short span would indicate a real capture gap",
            "nonzero_volume_bars": len(nonzero_vol), "nonzero_volume_pct": round(len(nonzero_vol) / len(df), 4),
            "nonzero_volume_first": str(nonzero_vol.index.min()) if len(nonzero_vol) else None,
            "nonzero_volume_last": str(nonzero_vol.index.max()) if len(nonzero_vol) else None,
            "volume_note": "INDEX kind: volume is a Dhan-attached proxy figure, sparse/intermittent "
                           "even within its own date range (verified live 2026-09-29) -- every "
                           "delta/cvd/absorption-based signal is only as good as this sparse volume "
                           "coverage, NOT the full price-history date range shown above",
            "is_proxy": self.is_proxy, "mode": self.mode,
        }
