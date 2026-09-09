"""Causal, pure-Python indicators for ORDERFLOW_ENGINE v1. value[i] depends
only on inputs <= i. Lists are input-length; undefined positions are None."""
from __future__ import annotations
import math


def ema(x: list[float], n: int) -> list[float]:
    k = 2.0 / (n + 1.0)
    out, e = [], None
    for v in x:
        e = v if e is None else v * k + e * (1.0 - k)
        out.append(e)
    return out


def sma(x, n):
    out = [None] * len(x)
    s = 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def _rma(x, n):
    out = [None] * len(x)
    a = 1.0 / n
    r, acc, cnt = None, 0.0, 0
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


def true_range(h, l, c):
    out = [h[0] - l[0]]
    for i in range(1, len(h)):
        pc = c[i - 1]
        out.append(max(h[i] - l[i], abs(h[i] - pc), abs(l[i] - pc)))
    return out


def atr(h, l, c, n):
    return _rma(true_range(h, l, c), n)


def rsi(closes, n):
    ch = [None] + [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    up = [None if v is None else max(v, 0.0) for v in ch]
    dn = [None if v is None else -min(v, 0.0) for v in ch]
    ru, rd = _rma(up, n), _rma(dn, n)
    out = []
    for u, d in zip(ru, rd):
        if u is None or d is None:
            out.append(None)
        elif d == 0:
            out.append(100.0)
        else:
            out.append(100.0 - 100.0 / (1.0 + u / d))
    return out


def adx(h, l, c, n):
    up = [None] + [h[i] - h[i - 1] for i in range(1, len(h))]
    dn = [None] + [l[i - 1] - l[i] for i in range(1, len(l))]
    pdm = [None if u is None else (u if (u > d and u > 0) else 0.0) for u, d in zip(up, dn)]
    mdm = [None if d is None else (d if (d > u and d > 0) else 0.0) for u, d in zip(up, dn)]
    trur = _rma(true_range(h, l, c), n)
    rp, rm = _rma(pdm, n), _rma(mdm, n)
    dx = []
    for a, b, t in zip(rp, rm, trur):
        if a is None or b is None or not t:
            dx.append(None)
            continue
        p, m = 100.0 * a / t, 100.0 * b / t
        s = p + m
        dx.append(100.0 * abs(p - m) / (s if s != 0 else 1.0))
    return _rma(dx, n)


def session_vwap_proxy(hlc3: list[float], sess: list[str]) -> list[float]:
    """Cash index has no volume -> session-anchored cumulative HLC3 mean
    (equal per-bar weight). method = PRICE_PROXY_EQUAL_WEIGHT."""
    out = []
    cur = None
    s = 0.0
    n = 0
    for p, d in zip(hlc3, sess):
        if d != cur:
            cur, s, n = d, 0.0, 0
        s += p
        n += 1
        out.append(s / n)
    return out


def efficiency_ratio(closes: list[float], n: int) -> list:
    out = [None] * len(closes)
    for i in range(n, len(closes)):
        net = abs(closes[i] - closes[i - n])
        path = sum(abs(closes[k] - closes[k - 1]) for k in range(i - n + 1, i + 1))
        out[i] = (net / path) if path > 1e-12 else 0.0
    return out


def pivots(h: list[float], l: list[float], w: int) -> tuple[list[int], list[int]]:
    """Fractal swing pivots. A pivot at i is only *known* at i+w (causal:
    callers must not use pivot i before bar i+w). Returns (high_idx, low_idx)."""
    hi, lo = [], []
    n = len(h)
    for i in range(w, n - w):
        seg_h = h[i - w:i + w + 1]
        seg_l = l[i - w:i + w + 1]
        if h[i] == max(seg_h) and seg_h.count(h[i]) == 1:
            hi.append(i)
        if l[i] == min(seg_l) and seg_l.count(l[i]) == 1:
            lo.append(i)
    return hi, lo


def pctl(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, int(q * (len(sorted_vals) - 1))))
    return sorted_vals[k]
