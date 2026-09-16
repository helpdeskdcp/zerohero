"""
Dynamic Quantitative Support/Resistance Engine — orchestrator.

    LIVE OHLCV -> swing high/low detection (pivots.py, reuses sr_engine._swings)
              -> price-distance clustering (clustering.py, reuses sr_engine._cluster)
              -> touch/rejection + volume confirmation (touches.py)
              -> breakout/retest/flip detection (breakout.py)
              -> multi-timeframe confirmation (mtf_confirm.py)
              -> final SR zones + 0-100 strength score (scoring.py)

Preserves the existing app.engines.sr_engine architecture: this is an
ADDITIVE module (new package, does not modify sr_engine.py), reusing its
private helpers (_bars, _swings, _cluster, _atr) exactly as
app.engines.state_classifier already does. compute_sr()/classify() are
untouched and continue to serve production.

No look-ahead: every input bar list must already be CLOSED bars only (the
same convention every caller in this codebase follows via
CandleAggregator's closed_only=True). Every sub-step (pivots, breakout/
retest/flip) is itself causal — see each module's own docstring and
no-lookahead tests. This orchestrator adds no new look-ahead: it never
reorders or peeks past the bars it's given.
"""
from __future__ import annotations

from ..engines.sr_engine import _atr, _bars
from .pivots import confirmed_swings
from .clustering import cluster_swings
from .touches import analyze_touches
from .breakout import analyze_breakout_retest_flip
from .mtf_confirm import confirmed_timeframes, TF_ORDER
from .scoring import score_zone

MODEL_VERSION = "sr-dynamic-v1"

DEFAULT_CONFIG = {
    "cluster_merge_atr_mult": 0.25,
    "left": 2, "right": 2, "lookback": 80,
    "mtf_tolerance_atr_mult": 0.3,
}


def _zones_for_tf(bars, cfg):
    """(zones, H, L, C, V, atr) for one timeframe's closed-bar series."""
    H, L, C, V, n = _bars(bars)
    if n < cfg["left"] + cfg["right"] + 1:
        return [], H, L, C, V, None
    atr = _atr(H, L, C, n, min(14, n - 1))
    if not atr or atr <= 0:
        return [], H, L, C, V, atr
    swings = confirmed_swings(bars, left=cfg["left"], right=cfg["right"], lookback=cfg["lookback"])
    zones = cluster_swings(swings, n_bars=n, merge=cfg["cluster_merge_atr_mult"] * atr)
    return zones, H, L, C, V, atr


def compute_dynamic_sr(bars_by_tf: dict, *, primary_tf: str = "5m", config: dict | None = None) -> list[dict]:
    """Returns a list of zone dicts (see module docstring for the pipeline
    and the output contract) for `primary_tf`, cross-confirmed against
    whichever other timeframes in TF_ORDER are also present in
    `bars_by_tf`. Returns [] if `primary_tf` has too little data -- never
    fabricates a zone."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    primary_bars = bars_by_tf.get(primary_tf) or []
    zones, H, L, C, V, atr = _zones_for_tf(primary_bars, cfg)
    if not zones or atr is None:
        return []

    zones_by_tf = {primary_tf: zones}
    for tf in TF_ORDER:
        if tf == primary_tf or tf not in bars_by_tf:
            continue
        other_zones, *_ = _zones_for_tf(bars_by_tf[tf], cfg)
        if other_zones:
            zones_by_tf[tf] = other_zones
    other_tfs_available = [tf for tf in zones_by_tf if tf != primary_tf]

    price = C[-1]
    out = []
    for z in zones:
        ta = analyze_touches(z.zone_low, z.zone_high, H, L, C, V, atr)
        bo = analyze_breakout_retest_flip(z.zone_low, z.zone_high, C, atr)
        tfs_confirmed = confirmed_timeframes(
            z.level, {tf: zl for tf, zl in zones_by_tf.items() if tf != primary_tf},
            atr=atr, tolerance_atr_mult=cfg["mtf_tolerance_atr_mult"])
        # this zone always confirms on its own source timeframe
        tfs_confirmed_all = [t for t in TF_ORDER if t == primary_tf or t in tfs_confirmed]

        sc = score_zone(
            swing_count=z.swing_count, touches=ta.touches, rejections=ta.rejections,
            volume_ratio=ta.volume_ratio, last_touch_index=ta.last_touch_index, n_bars=len(C),
            retest_status=bo.retest_status,
            confirmed_other_tfs=len(tfs_confirmed) if other_tfs_available else None,
            n_other_tfs_available=len(other_tfs_available) if other_tfs_available else None,
        )

        out.append({
            "level": round(z.level, 4),
            "zone_low": z.zone_low,
            "zone_high": z.zone_high,
            "type": "SUPPORT" if z.level < price else "RESISTANCE",
            "strength": sc.strength,
            "touches": ta.touches,
            "rejections": ta.rejections,
            "rejection_strength": ta.rejection_strength,
            "volume_ratio": ta.volume_ratio,
            "breakout_status": bo.breakout_status,
            "retest_status": bo.retest_status,
            "flip_status": bo.flip_status,
            "breakout_count": bo.breakout_count,
            "successful_retest_count": bo.successful_retest_count,
            "failed_breakout_count": bo.failed_breakout_count,
            "timeframes_confirmed": tfs_confirmed_all,
            "components": sc.components,
            "model_version": MODEL_VERSION,
        })
    return sorted(out, key=lambda d: d["level"])
