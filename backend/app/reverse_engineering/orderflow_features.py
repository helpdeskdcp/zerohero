"""
Phase 5 -- deterministic orderflow-adjacent features for research events.

Every function here is pure (no I/O) and documented. Functions 1-4 below
compute real values from data this codebase actually captures. Functions
5-9 are HONEST STUBS: this codebase has no tick-level/aggressor trade data
anywhere (confirmed repeatedly this session -- see memory
orderflow-l2-imbalance-gate.md and orderflow-h1h7-options-research.md, and
app/orderflow/depth.py's own docstring), so "aggressive buy/sell pressure",
true volume delta, cumulative delta, absorption, and sweep/aggression
cannot be computed from anything real. They return
{"available": False, "reason": "NO_TICK_LEVEL_DATA"} rather than an
approximation dressed up as the real thing.
"""
from __future__ import annotations

from ..orderflow import depth as _depth

_EPS = 1e-9

# ---------------------------------------------------------------- REAL (1-4)


def depth_imbalance(symbol: str, *, at_or_before: str | None = None) -> dict:
    """1/2. Bid/ask (resting order-book) imbalance + spread state --
    delegates entirely to app.orderflow.depth (already real, already
    tested, already honestly labeled as RESTING not aggressor flow).
    Not reimplemented here to avoid a second, possibly-diverging copy."""
    return _depth.snapshot_for_symbol(symbol, at_or_before=at_or_before)


def premium_momentum(ltp_series: list[tuple[str, float]]) -> dict:
    """3. Premium momentum/acceleration from consecutive REAL option_ltp
    captures (ts, ltp) pairs, chronological. Momentum = simple % change
    over the series; acceleration = change in that % change (2nd
    difference) -- both undefined (None) with fewer than 2/3 real points
    respectively, never guessed."""
    pts = [(ts, v) for ts, v in ltp_series if v is not None]
    if len(pts) < 2:
        return {"available": False, "reason": "fewer than 2 real ltp points",
                "momentum_pct": None, "acceleration_pct": None}
    first_v, last_v = pts[0][1], pts[-1][1]
    momentum_pct = round((last_v - first_v) / first_v * 100.0, 4) if abs(first_v) > _EPS else None
    accel = None
    if len(pts) >= 3:
        mids = [(pts[i][1] - pts[i - 1][1]) / pts[i - 1][1] * 100.0
               for i in range(1, len(pts)) if abs(pts[i - 1][1]) > _EPS]
        if len(mids) >= 2:
            accel = round(mids[-1] - mids[0], 4)
    return {"available": True, "momentum_pct": momentum_pct, "acceleration_pct": accel,
           "n_points": len(pts)}


def oi_price_relationship(oi_series: list[tuple[str, float]],
                          price_series: list[tuple[str, float]]) -> dict:
    """4a. Classic OI-price read (long build-up / short build-up / long
    unwind / short covering), from REAL oi + price deltas over the same
    window. Needs at least 2 real points in BOTH series covering an
    overlapping span -- otherwise DATA_UNAVAILABLE, never guessed."""
    oi_pts = [(t, v) for t, v in oi_series if v is not None]
    px_pts = [(t, v) for t, v in price_series if v is not None]
    if len(oi_pts) < 2 or len(px_pts) < 2:
        return {"available": False, "reason": "fewer than 2 real oi or price points",
                "relationship": None}
    d_oi = oi_pts[-1][1] - oi_pts[0][1]
    d_px = px_pts[-1][1] - px_pts[0][1]
    if d_oi > 0 and d_px > 0:
        rel = "LONG_BUILDUP"
    elif d_oi > 0 and d_px < 0:
        rel = "SHORT_BUILDUP"
    elif d_oi < 0 and d_px < 0:
        rel = "LONG_UNWINDING"
    elif d_oi < 0 and d_px > 0:
        rel = "SHORT_COVERING"
    else:
        rel = "FLAT"
    return {"available": True, "relationship": rel, "delta_oi": d_oi, "delta_price": d_px}


def price_volume_divergence(price_series: list[tuple[str, float]],
                            volume_series: list[tuple[str, float]]) -> dict:
    """4b. Price rising on falling volume (or vice versa) from REAL data
    only. Needs >=2 real points in both series."""
    px_pts = [v for _, v in price_series if v is not None]
    vol_pts = [v for _, v in volume_series if v is not None]
    if len(px_pts) < 2 or len(vol_pts) < 2:
        return {"available": False, "reason": "fewer than 2 real price or volume points",
                "divergence": None}
    d_px = px_pts[-1] - px_pts[0]
    d_vol = vol_pts[-1] - vol_pts[0]
    divergence = bool((d_px > 0 and d_vol < 0) or (d_px < 0 and d_vol > 0))
    return {"available": True, "divergence": divergence, "delta_price": d_px, "delta_volume": d_vol}


# ------------------------------------------------------- HONEST STUBS (5-9)
# No tick-level/aggressor trade data exists anywhere in this codebase.
# Returning a fabricated approximation here (e.g. inferring aggressor side
# from a price uptick) would be exactly the kind of manufactured-precision
# this project's own rules prohibit -- so these are explicit stubs.

_NO_TICK_DATA = {"available": False, "reason": "NO_TICK_LEVEL_DATA"}


def aggressive_buy_sell_pressure(*_args, **_kwargs) -> dict:
    return dict(_NO_TICK_DATA)


def volume_delta(*_args, **_kwargs) -> dict:
    return dict(_NO_TICK_DATA)


def cumulative_delta(*_args, **_kwargs) -> dict:
    return dict(_NO_TICK_DATA)


def absorption(*_args, **_kwargs) -> dict:
    return dict(_NO_TICK_DATA)


def sweep_aggression(*_args, **_kwargs) -> dict:
    return dict(_NO_TICK_DATA)


def liquidity_imbalance(symbol: str, *, at_or_before: str | None = None) -> dict:
    """Distinct from depth_imbalance's directional read -- this is the
    resting liquidity_state (THIN/NORMAL/DEEP) app.orderflow.depth already
    computes from the same resting book. Delegated, not duplicated."""
    snap = _depth.snapshot_for_symbol(symbol, at_or_before=at_or_before)
    return {"available": snap.get("available", False), "liquidity_state": snap.get("liquidity_state"),
           "note": snap.get("note")}


def breakout_retest_behaviour(*_args, **_kwargs) -> dict:
    """13. Breakout/retest needs a defined level + real bars around it --
    left as a stub here since it depends on regime.py's level detection,
    not on orderflow data; callers should use regime.classify_regime's
    structure output instead of calling this directly."""
    return {"available": False, "reason": "use app.reverse_engineering.regime for structure"}
