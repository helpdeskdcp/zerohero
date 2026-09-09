"""Causal, pure-Python ports of the Pine `ta.*` functions used by SSL Hybrid PRO.
Every function returns a list the same length as the input; not-yet-defined
positions are None. No look-ahead: value[i] depends only on inputs <= i.
"""
from __future__ import annotations
import math


def ema(x: list[float], n: int) -> list[float]:
    """Pine ta.ema -- seeded with the first value, k = 2/(n+1)."""
    k = 2.0 / (n + 1.0)
    out: list = []
    e = None
    for v in x:
        e = v if e is None else v * k + e * (1.0 - k)
        out.append(e)
    return out


def sma(x: list[float], n: int) -> list:
    out = [None] * len(x)
    s = 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def wma(x: list, n: int) -> list:
    """Linear weights 1..n (most recent heaviest). None if any window slot is None."""
    w = list(range(1, n + 1))
    sw = float(sum(w))
    out = [None] * len(x)
    for i in range(n - 1, len(x)):
        seg = x[i - n + 1:i + 1]
        if any(v is None for v in seg):
            continue
        out[i] = sum(a * b for a, b in zip(seg, w)) / sw
    return out


def hma(x: list[float], n: int) -> list:
    """Pine ta.hma = wma(2*wma(x, n/2) - wma(x, n), floor(sqrt(n)))."""
    n2 = max(1, int(n // 2))
    sq = max(1, int(math.floor(math.sqrt(n))))
    w1 = wma(x, n2)
    w2 = wma(x, n)
    raw = [None if (a is None or b is None) else 2.0 * a - b for a, b in zip(w1, w2)]
    return wma(raw, sq)


def rma(x: list, n: int) -> list:
    """Wilder's moving average (Pine ta.rma). Seeded with the SMA of the first
    n non-None values; carries the last value across a None input."""
    out = [None] * len(x)
    a = 1.0 / n
    r = None
    acc = 0.0
    cnt = 0
    for i, v in enumerate(x):
        if v is None:
            out[i] = r
            continue
        if r is None:
            acc += v
            cnt += 1
            if cnt == n:
                r = acc / n
                out[i] = r
        else:
            r = a * v + (1.0 - a) * r
            out[i] = r
    return out


def true_range(h, l, c) -> list[float]:
    out = [h[0] - l[0]]
    for i in range(1, len(h)):
        pc = c[i - 1]
        out.append(max(h[i] - l[i], abs(h[i] - pc), abs(l[i] - pc)))
    return out


def atr(h, l, c, n) -> list:
    return rma(true_range(h, l, c), n)


def rsi(closes: list[float], n: int) -> list:
    ch = [None] + [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    up = [None if v is None else max(v, 0.0) for v in ch]
    dn = [None if v is None else -min(v, 0.0) for v in ch]
    ru, rd = rma(up, n), rma(dn, n)
    out = []
    for u, d in zip(ru, rd):
        if u is None or d is None:
            out.append(None)
        elif d == 0:
            out.append(100.0)
        else:
            rs = u / d
            out.append(100.0 - 100.0 / (1.0 + rs))
    return out


def dmi(h, l, c, di_len: int, adx_len: int):
    """Pine ta.dmi -> (+DI, -DI, ADX)."""
    up = [None] + [h[i] - h[i - 1] for i in range(1, len(h))]
    dn = [None] + [l[i - 1] - l[i] for i in range(1, len(l))]
    plus_dm = [None if u is None else (u if (u > d and u > 0) else 0.0) for u, d in zip(up, dn)]
    minus_dm = [None if d is None else (d if (d > u and d > 0) else 0.0) for u, d in zip(up, dn)]
    trur = rma(true_range(h, l, c), di_len)
    rp, rm = rma(plus_dm, di_len), rma(minus_dm, di_len)
    plus, minus = [], []
    for a, b, t in zip(rp, rm, trur):
        plus.append(None if (a is None or not t) else 100.0 * a / t)
        minus.append(None if (b is None or not t) else 100.0 * b / t)
    dx = []
    for p, m in zip(plus, minus):
        if p is None or m is None:
            dx.append(None)
        else:
            s = p + m
            dx.append(100.0 * abs(p - m) / (s if s != 0 else 1.0))
    return plus, minus, rma(dx, adx_len)


def session_vwap(typ: list[float], vol: list[float], sess: list[str],
                 *, anchored: bool, use_volume: bool) -> list[float]:
    """VWAP of the typical price. If `use_volume` is False (cash-index history
    has none), fall back to a cumulative equal-weight mean of `typ` -- the
    disclosed price-VWAP proxy. Resets each session day when `anchored`."""
    out = []
    cur = None
    pv = 0.0
    vv = 0.0
    for t, v, s in zip(typ, vol, sess):
        if anchored and s != cur:
            cur = s
            pv = 0.0
            vv = 0.0
        w = (v if (use_volume and v and v > 0) else 1.0)
        pv += t * w
        vv += w
        out.append(pv / vv if vv else t)
    return out


def crossover(a: list, b: list) -> list[bool]:
    out = [False] * len(a)
    for i in range(1, len(a)):
        if None in (a[i], b[i], a[i - 1], b[i - 1]):
            continue
        out[i] = a[i - 1] <= b[i - 1] and a[i] > b[i]
    return out


def crossunder(a: list, b: list) -> list[bool]:
    out = [False] * len(a)
    for i in range(1, len(a)):
        if None in (a[i], b[i], a[i - 1], b[i - 1]):
            continue
        out[i] = a[i - 1] >= b[i - 1] and a[i] < b[i]
    return out
