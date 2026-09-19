"""
Historical validation of app.sr_dynamic (the new Dynamic Quantitative SR
Engine) against real data, and a structural comparison with the existing
app.engines.sr_engine.

Data:
  - NATGAS: real MCX futures 5m bars already built for this session's
    NATGAS backtest (data/research/natgas_futures_backtest/natgas_futures_5m_bars.json,
    3.75 months, 2026-05-20 -> 2026-09-10).
  - NIFTY: real Kaggle 5m index bars (app.liquidity_sweep.backtest.load_kaggle_nifty_bars),
    most recent 1 year for tractability (documented scope reduction).

Since analyze_breakout_retest_flip() already walks the WHOLE bar array
causally in one pass (bar i's classification only ever depends on bars
0..i), a single compute_dynamic_sr() call on the full historical series
already IS the walk-forward result -- no separate rolling-window loop is
needed to get real, causally-computed breakout/retest/flip counts.

"Performance by timeframe" resamples the same real 5m bars up to 15m/30m
with simple non-overlapping bucketing (fine for an after-the-fact backtest
report; the anti-repaint resample_confirmed machinery in
app.strategy_mtf.htf_resample exists for LIVE decisioning, not needed here).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.engines.sr_engine import compute_sr
from app.liquidity_sweep.backtest import load_kaggle_nifty_bars
from app.sr_dynamic.breakout import analyze_breakout_retest_flip
from app.sr_dynamic.engine import (
    DEFAULT_CONFIG,
    _zones_for_tf,
    compute_dynamic_sr,
)
from app.sr_dynamic.touches import analyze_touches

NATGAS_5M = (Path(__file__).parents[1] / "data" / "research" / "natgas_futures_backtest" /
            "natgas_futures_5m_bars.json")


def _resample(bars_5m: list[dict], factor: int) -> list[dict]:
    out = []
    for i in range(0, len(bars_5m) - factor + 1, factor):
        chunk = bars_5m[i:i + factor]
        out.append({
            "t": chunk[0]["t"], "o": chunk[0]["o"], "c": chunk[-1]["c"],
            "h": max(b["h"] for b in chunk), "l": min(b["l"] for b in chunk),
            "v": sum(b.get("v", 0.0) for b in chunk),
        })
    return out


def _zone_stats(zones: list[dict]) -> dict:
    n = len(zones)
    if n == 0:
        return {"n_zones": 0}
    total_breakouts = sum(z["breakout_count"] for z in zones)
    total_success = sum(z["successful_retest_count"] for z in zones)
    total_failed = sum(z["failed_breakout_count"] for z in zones)
    touched = [z for z in zones if z["touches"] > 0]
    reaction_rate = mean(z["rejections"] / z["touches"] for z in touched) if touched else None
    strengths = [z["strength"] for z in zones if z.get("strength") is not None]
    return {
        "n_zones": n,
        "avg_strength": round(mean(strengths), 1) if strengths else None,
        "total_breakouts": total_breakouts,
        "successful_retest_pct": round(100.0 * total_success / total_breakouts, 1) if total_breakouts else None,
        "false_breakout_pct": round(100.0 * total_failed / total_breakouts, 1) if total_breakouts else None,
        # breakouts still PENDING (neither confirmed successful nor failed yet
        # as of the last real bar) are the remainder -- reported explicitly so
        # the two percentages above are never mistaken for summing to 100%.
        "pending_breakout_pct": round(100.0 * (total_breakouts - total_success - total_failed)
                                      / total_breakouts, 1) if total_breakouts else None,
        "support_resistance_reaction_rate_pct": round(100.0 * reaction_rate, 1) if reaction_rate is not None else None,
        "avg_touches_per_zone": round(mean(z["touches"] for z in zones), 2),
        "avg_volume_ratio": round(mean(z["volume_ratio"] for z in zones if z["volume_ratio"] is not None), 3)
                            if any(z["volume_ratio"] is not None for z in zones) else None,
        "zones_confirmed_on_2plus_tf": sum(1 for z in zones if len(z["timeframes_confirmed"]) >= 2),
    }


def _existing_engine_stats(bars_5m: list[dict], *, mode: str) -> dict:
    """Existing sr_engine.compute_sr is a single-snapshot scorer (no
    breakout/retest/MTF state) -- sampled at regular points through history
    for a structural comparison, not a like-for-like backtest."""
    n_levels = []
    strengths = []
    step = max(1, len(bars_5m) // 40)
    for i in range(50, len(bars_5m), step):
        sr = compute_sr({"5m": bars_5m[:i]}, mode=mode)
        if sr.get("status") != "OK":
            continue
        levels = sr.get("levels") or []
        n_levels.append(len(levels))
        strengths.extend(l["strength"] for l in levels if l.get("strength") is not None)
    return {
        "n_snapshots": len(n_levels),
        "avg_levels_per_snapshot": round(mean(n_levels), 2) if n_levels else None,
        "avg_level_strength": round(mean(strengths), 1) if strengths else None,
    }


def rolling_zone_inventory(bars: list[dict], *, step: int = 200, merge_tolerance_mult: float = 1.5) -> list[dict]:
    """The live engine's swing detector only ever looks at the most recent
    80 bars (app.sr_dynamic.pivots / sr_engine._swings' `lookback` default)
    -- correct for live use, but a SINGLE call on a multi-month array would
    only ever see the last 6-7 hours of structure. This rolls the discovery
    step forward through history (as a live system naturally would, one
    decision cycle at a time), accumulating the union of zone levels ever
    visible at any point, then scores each discovered level ONCE against
    the FULL subsequent bar history for its true cumulative touch/breakout/
    retest record -- exactly what a live system does once it has spotted a
    level (keep tracking it going forward)."""
    cfg = DEFAULT_CONFIG
    from app.engines.sr_engine import _atr
    from app.engines.sr_engine import _bars as _parse_bars
    H_full, L_full, C_full, V_full, n = _parse_bars(bars)
    if n < 100:
        return []

    discovered_levels: list[float] = []
    for i in range(100, n, step):
        zones, H, L, C, V, atr = _zones_for_tf(bars[:i], cfg)
        if atr is None:
            continue
        tol = merge_tolerance_mult * atr
        for z in zones:
            if not any(abs(z.level - lvl) <= tol for lvl in discovered_levels):
                discovered_levels.append(z.level)

    atr_full = _atr(H_full, L_full, C_full, n, min(14, n - 1))
    if not atr_full or atr_full <= 0:
        return []
    price = C_full[-1]
    out = []
    zone_half_width = cfg["cluster_merge_atr_mult"] * atr_full / 2.0
    for lvl in discovered_levels:
        zlo, zhi = lvl - zone_half_width, lvl + zone_half_width
        ta = analyze_touches(zlo, zhi, H_full, L_full, C_full, V_full, atr_full)
        bo = analyze_breakout_retest_flip(zlo, zhi, C_full, atr_full)
        out.append({
            "level": round(lvl, 4), "zone_low": round(zlo, 4), "zone_high": round(zhi, 4),
            "type": "SUPPORT" if lvl < price else "RESISTANCE",
            "touches": ta.touches, "rejections": ta.rejections, "volume_ratio": ta.volume_ratio,
            "breakout_count": bo.breakout_count, "successful_retest_count": bo.successful_retest_count,
            "failed_breakout_count": bo.failed_breakout_count,
            "breakout_status": bo.breakout_status, "retest_status": bo.retest_status,
            "flip_status": bo.flip_status, "timeframes_confirmed": [],
            "strength": None,   # not scored here -- this is the structural-inventory pass
        })
    return out


def run_instrument(name: str, bars_5m: list[dict], *, mode: str) -> dict:
    result = {"instrument": name, "n_bars_5m": len(bars_5m)}

    tf_bars = {"5m": bars_5m, "15m": _resample(bars_5m, 3), "30m": _resample(bars_5m, 6)}
    result["by_timeframe"] = {}
    for tf, tf_step in (("5m", 200), ("15m", 70), ("30m", 35)):
        zones = rolling_zone_inventory(tf_bars[tf], step=tf_step)
        result["by_timeframe"][tf] = _zone_stats(zones)

    # single-call live-mode snapshot too (what a fresh boot sees right now,
    # for comparison against the rolled historical inventory above)
    result["live_snapshot_5m"] = _zone_stats(compute_dynamic_sr(tf_bars, primary_tf="5m"))
    result["dynamic_engine_5m"] = result["by_timeframe"]["5m"]
    result["existing_engine_5m"] = _existing_engine_stats(bars_5m, mode=mode)
    return result


def main():
    print("loading real NATGAS 5m bars...")
    natgas_bars = json.load(open(NATGAS_5M))
    print(f"  {len(natgas_bars)} bars")

    print("loading real NIFTY 5m bars (Kaggle, most recent 1 year)...")
    nifty_bars = load_kaggle_nifty_bars(limit_years=1.0)
    # The Kaggle dataset's tail is a flat-lined placeholder (H==L==C repeated,
    # a known data-refresh artifact, not real market data) -- found by this
    # validation run: 25 trailing bars stuck at 23649.95 with zero range,
    # which correctly drove ATR to 0 and (correctly) yielded zero zones on
    # that dead tail. Trimmed to the last bar with real H!=L movement so the
    # backtest measures real price action only.
    last_real = max(i for i, b in enumerate(nifty_bars) if b["h"] != b["l"])
    trimmed = len(nifty_bars) - 1 - last_real
    nifty_bars = nifty_bars[:last_real + 1]
    print(f"  {len(nifty_bars)} bars (trimmed {trimmed} flat-lined placeholder bars off the tail)")

    # mode="index" for both, matching what production actually calls (scalp_strategy.py
    # always passes mode="index" regardless of symbol -- see this session's NATGAS
    # diagnostic, which flagged the resulting round_step=50 default as a real but
    # separate scale mismatch for MCX symbols, not something to silently correct here).
    report = {
        "NATGAS": run_instrument("NATGAS", natgas_bars, mode="index"),
        "NIFTY": run_instrument("NIFTY", nifty_bars, mode="index"),
    }

    out_path = Path(__file__).parents[1] / "data" / "research" / "sr_dynamic" / "validation_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))
    print(f"\nsaved to {out_path}")


if __name__ == "__main__":
    main()
