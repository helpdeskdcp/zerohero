"""Canonical ATR / EMA (TradingView definitions). Eight ATR and nine EMA copies elsewhere in `app/` were measured identical to these (scripts/indicator_parity_audit.py, 2026-10-30), so
new code should import from here, and tests/test_indicator_parity.py fails if any copy drifts. (Two other ATR copies - reverse_engineering.regime and research_engines.order_pressure - use a
different smoothing on purpose and are NOT covered.) Pure numpy, no I/O."""
from __future__ import annotations

import numpy as np


def true_range(h, l, c) -> np.ndarray:
    h, l, c = (np.asarray(x, float) for x in (h, l, c))
    return np.r_[h[0] - l[0], np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])])]


def atr(h, l, c, n: int = 14) -> np.ndarray:
    """Wilder RMA of the true range, seeded with the simple mean of the first n true ranges (ta.atr); NaN before bar n."""
    tr = true_range(h, l, c); out = np.full(len(tr), np.nan)
    if len(tr) < n: return out
    out[n - 1] = tr[:n].mean()
    for i in range(n, len(tr)): out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def ema(x, n: int) -> np.ndarray:
    """alpha = 2 / (n + 1), seeded with the first value (pandas ewm(span=n, adjust=False))."""
    x = np.asarray(x, float); out = np.empty(len(x))
    if not len(x): return out
    a = 2.0 / (n + 1); out[0] = x[0]
    for i in range(1, len(x)): out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out
