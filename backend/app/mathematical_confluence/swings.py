"""
Causal swing detection (section 4 / 27).

A swing pivot at bar i needs `n` bars on BOTH sides. It is therefore only
CONFIRMED at bar i+n — never earlier. `detect_swings` scanning a series returns
only pivots confirmed at or before the last bar, so a real-time caller can never
see a swing that uses future bars.
"""
from __future__ import annotations


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def detect_swings(bars: list[dict], *, n: int = 2, now_index: int | None = None):
    """bars: [{high, low, close}] oldest-first. `now_index` = the last bar the
    caller is allowed to know about (default: len-1). Returns
    {swing_highs:[{index, price, confirmed_at}], swing_lows:[...],
     last_swing_high, last_swing_low}. A pivot is included only if
     confirmed_at <= now_index (no look-ahead)."""
    if now_index is None:
        now_index = len(bars) - 1
    highs = [_f(b.get("high")) for b in bars]
    lows = [_f(b.get("low")) for b in bars]
    sh, sl = [], []
    for i in range(n, len(bars) - n):
        confirmed_at = i + n
        if confirmed_at > now_index:
            break
        h = highs[i]
        if h is not None and all(highs[j] is not None and h >= highs[j] for j in range(i - n, i + n + 1) if j != i):
            sh.append({"index": i, "price": round(h, 4), "confirmed_at": confirmed_at})
        lo = lows[i]
        if lo is not None and all(lows[j] is not None and lo <= lows[j] for j in range(i - n, i + n + 1) if j != i):
            sl.append({"index": i, "price": round(lo, 4), "confirmed_at": confirmed_at})
    return {
        "swing_highs": sh, "swing_lows": sl,
        "last_swing_high": sh[-1]["price"] if sh else None,
        "last_swing_low": sl[-1]["price"] if sl else None,
        "recent_swing_high": sh[-2]["price"] if len(sh) >= 2 else None,
        "recent_swing_low": sl[-2]["price"] if len(sl) >= 2 else None,
    }


def swing_stats(bars: list[dict], level: float, *, tol_pct: float = 0.0015, now_index: int | None = None):
    """touch / rejection counts for a price `level` using only bars <= now_index."""
    if now_index is None:
        now_index = len(bars) - 1
    tol = level * tol_pct
    touch = reject = 0
    last_touch_i = None
    for i, b in enumerate(bars[:now_index + 1]):
        h, lo, c = _f(b.get("high")), _f(b.get("low")), _f(b.get("close"))
        if None in (h, lo, c):
            continue
        if lo - tol <= level <= h + tol:
            touch += 1
            last_touch_i = i
            # rejection = wick through but close back on the origin side
            if (h - tol > level and c < level) or (lo + tol < level and c > level):
                reject += 1
    return {
        "swing_touch_count": touch,
        "swing_rejection_count": reject,
        "swing_age": (now_index - last_touch_i) if last_touch_i is not None else None,
        "swing_strength": round(min(100.0, touch * 15 + reject * 20), 1),
    }
