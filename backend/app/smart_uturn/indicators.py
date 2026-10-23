"""Pine-faithful indicators on numpy arrays (no NaN allowed inside a series; leading NaN allowed).
ta.ema   : seeded with the first source value (Pine's own definition).
ta.rma   : seeded with the SMA of the first `n` valid values, then alpha = 1/n.
ta.rsi   : rma(up)/rma(down) of the 1-bar change; the first change is NaN, so the first valid RSI is at bar index n (not earlier), no NaN leaks afterwards.
           down == 0 -> 100 (also for a flat series: Pine's own formula), else up == 0 -> 0.
ta.atr   : rma(true range, n); the first true range is high-low (no previous close).
ta.lowest/highest(x, n) : NaN until n values exist.
ta.pivothigh/low(l, r)  : value of the bar `r` bars back, reported at the bar where it becomes confirmed (r bars later). Tie rule (equal highs both count) is NOT verified against TradingView."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import lfilter


def _check(x):
    x = np.asarray(x, dtype=float)
    ok = ~np.isnan(x)
    if not ok.any():
        return x, None
    f = int(np.argmax(ok))
    if np.isnan(x[f:]).any():
        raise ValueError("NaN inside the series (indicators never interpolate market data)")
    return x, f


def ema(src, n: int) -> np.ndarray:
    x, f = _check(src); out = np.full(x.shape, np.nan)
    if f is None: return out
    a = 2.0 / (n + 1); seg = x[f:]
    out[f:], _ = lfilter([a], [1.0, -(1.0 - a)], seg, zi=[(1.0 - a) * seg[0]])
    return out


def rma(src, n: int) -> np.ndarray:
    x, f = _check(src); out = np.full(x.shape, np.nan)
    if f is None or len(x) - f < n: return out
    seg = x[f:]; seed = float(seg[:n].mean()); out[f + n - 1] = seed
    if len(seg) > n:
        a = 1.0 / n
        out[f + n:], _ = lfilter([a], [1.0, -(1.0 - a)], seg[n:], zi=[(1.0 - a) * seed])
    return out


def sma(src, n: int) -> np.ndarray:
    return pd.Series(np.asarray(src, dtype=float)).rolling(n).mean().to_numpy()


def shift(x, k: int = 1, fill=np.nan) -> np.ndarray:
    x = np.asarray(x); out = np.full(x.shape, fill, dtype=float if not x.dtype == bool else object)
    if k < len(x): out[k:] = x[:len(x) - k]
    return out


def rsi(close, n: int) -> np.ndarray:
    c = np.asarray(close, dtype=float); ch = np.full(c.shape, np.nan); ch[1:] = np.diff(c)
    up = rma(np.where(np.isnan(ch), np.nan, np.maximum(ch, 0.0)), n); dn = rma(np.where(np.isnan(ch), np.nan, -np.minimum(ch, 0.0)), n)
    out = np.full(c.shape, np.nan); ok = ~np.isnan(up) & ~np.isnan(dn)
    with np.errstate(divide="ignore", invalid="ignore"):
        val = np.where(dn == 0, 100.0, np.where(up == 0, 0.0, 100.0 - 100.0 / (1.0 + up / dn)))
    out[ok] = val[ok]
    return out


def true_range(h, l, c) -> np.ndarray:
    h, l, c = (np.asarray(a, dtype=float) for a in (h, l, c)); tr = h - l
    if len(c) > 1: tr[1:] = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    return tr


def atr(h, l, c, n: int) -> np.ndarray:
    return rma(true_range(h, l, c), n)


def lowest(x, n: int) -> np.ndarray:
    return pd.Series(np.asarray(x, dtype=float)).rolling(n).min().to_numpy()


def highest(x, n: int) -> np.ndarray:
    return pd.Series(np.asarray(x, dtype=float)).rolling(n).max().to_numpy()


def lowest_prev(x, n: int) -> np.ndarray:
    """ta.lowest(x[1], n): the minimum of the n bars BEFORE the current one."""
    return lowest(shift(np.asarray(x, dtype=float), 1), n)


def highest_prev(x, n: int) -> np.ndarray:
    return highest(shift(np.asarray(x, dtype=float), 1), n)


def pivot_high(h, left: int, right: int) -> np.ndarray:
    h = np.asarray(h, dtype=float); n = len(h); out = np.full(n, np.nan)
    if n < left + right + 1: return out
    wmax = pd.Series(h).rolling(left + right + 1).max().to_numpy(); idx = np.arange(left + right, n)
    out[idx] = np.where(h[idx - right] == wmax[idx], h[idx - right], np.nan)
    return out


def pivot_low(l, left: int, right: int) -> np.ndarray:
    l = np.asarray(l, dtype=float); n = len(l); out = np.full(n, np.nan)
    if n < left + right + 1: return out
    wmin = pd.Series(l).rolling(left + right + 1).min().to_numpy(); idx = np.arange(left + right, n)
    out[idx] = np.where(l[idx - right] == wmin[idx], l[idx - right], np.nan)
    return out


def valuewhen_recent(event_values, k: int) -> np.ndarray:
    """ta.valuewhen(not na(v), v, k): the k-th most recent non-NaN event value as of each bar (k=0 latest)."""
    v = np.asarray(event_values, dtype=float); out = np.full(v.shape, np.nan); hist: list[float] = []
    for i in range(len(v)):
        if not np.isnan(v[i]): hist.append(v[i])
        if len(hist) > k: out[i] = hist[-1 - k]
    return out
