"""Real captured-option-premium lookups shared across data/research/*/
Phase-B tests (reversal_sweep, false_breakout_reversal, vwap_ema_scalp all
had a near-identical copy of nearest_expiry/premium_at)."""
from __future__ import annotations

import pandas as pd


def nearest_expiry(ts: pd.Timestamp, expiries: list[str]) -> str | None:
    """Nearest UN-expired expiry at `ts`, from a real captured list of
    expiry strings like '29SEP2026'."""
    parsed = [(pd.Timestamp(e, tz="Asia/Kolkata").normalize() + pd.Timedelta(hours=15, minutes=30), e)
             for e in expiries]
    future = [(d, e) for d, e in parsed if d >= ts]
    return min(future, key=lambda x: x[0])[1] if future else None


def premium_at(opt_ticks: pd.DataFrame, expiry: str, strike: float, option_type: str,
              ts: pd.Timestamp, tolerance_min: int = 30) -> float | None:
    """Real captured premium nearest `ts` for one specific contract, or
    None if nothing real was captured within `tolerance_min` -- never
    fabricates a price. `opt_ticks` from candles.load_captured_option_ticks()."""
    sub = opt_ticks[(opt_ticks["expiry"] == expiry) & (opt_ticks["strike"] == strike)
                    & (opt_ticks["option_type"] == option_type)]
    if sub.empty:
        return None
    idx = sub["ts"].searchsorted(ts)
    candidates = []
    if idx < len(sub):
        candidates.append(sub.iloc[idx])
    if idx > 0:
        candidates.append(sub.iloc[idx - 1])
    if not candidates:
        return None
    best = min(candidates, key=lambda r: abs((r["ts"] - ts).total_seconds()))
    if abs((best["ts"] - ts).total_seconds()) > tolerance_min * 60:
        return None
    return float(best["ltp"])
