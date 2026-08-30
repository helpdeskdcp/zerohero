"""
Probability calibration + reliability scoring for the autonomous scalper (spec-4,
spec-19).

Closed-form and deterministic (same rows in -> same curve out), mirroring the
tp_calibration approach: bucket the raw 0-100 signal score, take the empirical
win-rate per bucket, and fit a logistic  p = sigmoid(k*(s-0.5) + b)  by OLS on
logit(win-rate) ~ (s-0.5).  Separate curves per `regime|signal_type`, plus a
global fallback.  A curve needs >= `_MIN_ROWS` resolved outcomes or it is not
emitted (the caller falls back to the global / prior).

Nothing here trains on rows it will later be scored against — the P6 runner
passes disjoint chronological slices.
"""
from __future__ import annotations

import math
from collections import defaultdict

MODEL_VERSION = "scalp-calibration-v1"
_MIN_ROWS = 40
_K_LO, _K_HI = 1.0, 7.0
_B_LO, _B_HI = -1.5, 1.5


def _fit_logistic(rows):
    """rows: list of (score_0_100, win_bool). Returns {k,b,n,win_rate} or None."""
    rows = [(max(0.0, min(100.0, float(s))) / 100.0, 1 if w else 0) for s, w in rows if s is not None]
    n = len(rows)
    if n < _MIN_ROWS:
        return None
    buckets = defaultdict(lambda: [0, 0])          # rounded score -> [count, wins]
    for s, w in rows:
        b = buckets[round(s, 1)]
        b[0] += 1
        b[1] += w
    xs, ys = [], []
    for s, (cnt, wins) in buckets.items():
        if cnt < 4:
            continue
        rate = min(0.97, max(0.03, wins / cnt))
        xs.append(s - 0.5)
        ys.append(math.log(rate / (1 - rate)))
    win_rate = sum(w for _, w in rows) / n
    if len(xs) < 3:
        # not enough spread -> flat curve at the base rate
        b0 = math.log(min(0.97, max(0.03, win_rate)) / (1 - min(0.97, max(0.03, win_rate))))
        return {"k": 0.0, "b": round(max(_B_LO, min(_B_HI, b0)), 4), "n": n,
                "win_rate": round(win_rate, 4), "spread": len(xs)}
    m = len(xs)
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = m * sxx - sx * sx
    if abs(denom) < 1e-9:
        return {"k": 0.0, "b": round(sy / m, 4), "n": n, "win_rate": round(win_rate, 4), "spread": len(xs)}
    k = (m * sxy - sx * sy) / denom
    b = (sy - k * sx) / m
    return {"k": round(max(_K_LO, min(_K_HI, k)), 4),
            "b": round(max(_B_LO, min(_B_HI, b)), 4),
            "n": n, "win_rate": round(win_rate, 4), "spread": len(xs)}


def fit(samples, *, version: str | None = None) -> dict:
    """samples: iterable of dicts with keys score, regime, signal_type, win(bool)."""
    rows = [(s.get("score"), s.get("regime") or "?", s.get("signal_type") or "?", bool(s.get("win")))
            for s in samples if s.get("score") is not None]
    by_key = defaultdict(list)
    by_type = defaultdict(list)
    everything = []
    for score, regime, stype, win in rows:
        by_key[f"{regime}|{stype}"].append((score, win))
        by_type[f"*|{stype}"].append((score, win))
        everything.append((score, win))

    curves = {}
    for key, r in list(by_key.items()) + list(by_type.items()):
        c = _fit_logistic(r)
        if c:
            curves[key] = c
    glob = _fit_logistic(everything)          # None when < _MIN_ROWS -> use the prior
    return {
        "version": version or MODEL_VERSION,
        "n_samples": len(everything),
        "global": glob, "curves": curves,
        "fitted": glob is not None or bool(curves),
        "model_version": MODEL_VERSION,
    }


def _prior(s):
    """Conservative logistic prior, used until a real curve exists. Centre 0.58 /
    gentle slope: predicted probs stay close to the observed ~0.45-0.55 win
    rates of a thin scalping edge rather than over-claiming (spec-19)."""
    return 1.0 / (1.0 + math.exp(-(2.3 * (s - 0.58))))


def predict(calib: dict, score_0_100, *, regime: str = "?", signal_type: str = "?") -> float:
    s = max(0.0, min(100.0, float(score_0_100 or 0))) / 100.0
    curve = None
    if calib:
        curves = calib.get("curves") or {}
        curve = (curves.get(f"{regime}|{signal_type}") or curves.get(f"*|{signal_type}")
                 or calib.get("global"))
    # a missing or degenerate (k==0 and b==0) curve -> fall back to the prior,
    # never to a flat 0.5.
    if not curve or (curve.get("k", 0.0) == 0.0 and curve.get("b", 0.0) == 0.0):
        return _prior(s)
    return 1.0 / (1.0 + math.exp(-(curve.get("k", 0.0) * (s - 0.5) + curve.get("b", 0.0))))


def reliability_curve(pairs, bins: int = 10) -> dict:
    """pairs: list of (predicted_prob, win_bool). Returns bins + Brier score."""
    pairs = [(max(0.0, min(1.0, float(p))), 1 if w else 0) for p, w in pairs if p is not None]
    if not pairs:
        return {"bins": [], "brier": None, "n": 0}
    edges = [i / bins for i in range(bins + 1)]
    out = []
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        sel = [(p, w) for p, w in pairs if (lo <= p < hi) or (i == bins - 1 and p == hi)]
        if not sel:
            out.append({"bin": [round(lo, 2), round(hi, 2)], "n": 0,
                        "predicted": None, "actual": None})
            continue
        out.append({"bin": [round(lo, 2), round(hi, 2)], "n": len(sel),
                    "predicted": round(sum(p for p, _ in sel) / len(sel), 4),
                    "actual": round(sum(w for _, w in sel) / len(sel), 4)})
    brier = sum((p - w) ** 2 for p, w in pairs) / len(pairs)
    # expected calibration error
    ece = sum(b["n"] / len(pairs) * abs((b["predicted"] or 0) - (b["actual"] or 0))
              for b in out if b["n"])
    return {"bins": out, "brier": round(brier, 4), "ece": round(ece, 4), "n": len(pairs)}
