"""
Shared feature computation + base FSM classes for the 4 setups. Not one of
the spec's named setup files -- factored out so setup1..4 stay thin,
direction-mirrored subclasses instead of 4 independently-drifting copies of
the same breakout/reversal logic (a real risk: bull/bear mirrors silently
diverging is exactly the kind of bug that's easy to introduce and hard to
notice in duplicated code).

Every setup exposes the SAME lifecycle (spec Section 3):
  detect_context() -> detect_trigger() -> confirm() -> entry() -> stop() ->
  targets() -> invalidation()
as real methods on BreakoutSetup/ReversalSetup below; setup1..4 just fix
`direction` and read their own config block.

TIMESTAMP CONVENTION (spec C2, stated once here for all 4 setups): a signal
computed "as of bar i" may only use columns whose value at row i is knowable
from bar i's OWN close and everything before it (rolling windows, prior
bars) -- never bar i+1 or later. Entries always execute at bar i+1's OPEN,
never bar i's own close/open, enforced by scan()'s callers in
engine/backtester.py, not here -- but every column added below is
window-shifted so it CAN legally be looked at during bar i's own scan.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_context_columns(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Adds, once, the columns every setup's detect_context()/confirm() read.
    All rolling/shifted so a value at row i never leaks row i's own not-yet-
    closed information beyond what a bar-close signal is allowed to see
    (a rolling window ending at i, by construction, only uses i and earlier
    -- exactly what's known at bar i's close)."""
    out = df.copy()
    s = cfg["structure"]

    ema = out["close"].ewm(span=s["htf_ema_period"], adjust=False).mean()
    ema_slope = ema.diff(s["htf_slope_lookback"])
    out["htf_trend"] = np.where(ema_slope > 0, "bullish", np.where(ema_slope < 0, "bearish", "flat"))

    out["range_high"] = out["high"].rolling(s["range_lookback"]).max()
    out["range_low"] = out["low"].rolling(s["range_lookback"]).min()
    out["range_height_pct"] = (out["range_high"] - out["range_low"]) / out["close"] * 100

    out["avg_volume"] = out["volume"].rolling(20).mean()

    prior_close = out["close"].shift(1)
    true_range = pd.concat([out["high"] - out["low"], (out["high"] - prior_close).abs(),
                            (out["low"] - prior_close).abs()], axis=1).max(axis=1)
    out["atr"] = true_range.rolling(s["atr_lookback"]).mean()

    # volume-weighted "value area" proxy over volume_profile_lookback bars --
    # POC = the close price bucket with the most volume; VAH/VAL = the
    # narrowest price band containing value_area_pct of that volume. A real
    # bar-by-bar volume profile needs intrabar price distribution we don't
    # have (index OHLCV only) -- this approximates each bar's volume as
    # concentrated at its own close, which is the standard OHLCV-only proxy.
    def _vah_val_poc(window: pd.DataFrame):
        prices = window["close"].values
        vols = window["volume"].values
        if len(prices) == 0 or vols.sum() == 0:
            return np.nan, np.nan, np.nan
        order = np.argsort(prices)
        prices, vols = prices[order], vols[order]
        poc = prices[np.argmax(vols)]
        total = vols.sum()
        cum = np.cumsum(vols)
        lo_idx = np.searchsorted(cum, total * (1 - s["value_area_pct"]) / 2)
        hi_idx = np.searchsorted(cum, total * (1 + s["value_area_pct"]) / 2)
        lo_idx, hi_idx = min(lo_idx, len(prices) - 1), min(hi_idx, len(prices) - 1)
        return prices[lo_idx], prices[hi_idx], poc

    vah, val, poc = [], [], []
    lb = s["volume_profile_lookback"]
    win_cols = out[["close", "volume"]]
    for i in range(len(out)):
        if i + 1 < lb:
            vah.append(np.nan); val.append(np.nan); poc.append(np.nan)
            continue
        v, a, p = _vah_val_poc(win_cols.iloc[i + 1 - lb:i + 1])
        val.append(v); vah.append(a); poc.append(p)
    out["val"], out["vah"], out["poc"] = val, vah, poc
    return out


def add_setup_columns(df: pd.DataFrame, avg_delta_lookback: int, vol_lookback: int) -> pd.DataFrame:
    """Per-setup rolling columns (different setups use different lookbacks
    for avg_delta/vol, so this is called once per setup with its own config,
    not merged into compute_context_columns's shared pass)."""
    out = df.copy()
    out["avg_delta_abs"] = out["delta"].abs().rolling(avg_delta_lookback).mean()
    out["vol_ma_setup"] = out["volume"].rolling(vol_lookback).mean()
    return out


class SetupBase:
    """direction: "bull" or "bear". All sign-flips for the mirrored setup
    live in the small number of places marked `if self.bull:` below --
    everything else is shared, so setup1/setup2 (and setup3/setup4) cannot
    silently drift apart.

    full_cfg: the ENTIRE resolved config (so both this setup's own block AND
    the shared `structure:` block are reachable). setup_key: e.g.
    "setup1_bull_breakout" -- looked up in full_cfg for self.cfg. name_tag:
    the tag stamped on every trade this setup produces (report.py groups by
    this)."""
    name: str = "base"

    def __init__(self, full_cfg: dict, setup_key: str, direction: str, name_tag: str):
        self.cfg = full_cfg[setup_key]
        self.structure_cfg = full_cfg["structure"]
        self.full_cfg = full_cfg  # needed for sm_context.compute_context (cfg["smartmoney"])
        self.direction = direction
        self.bull = direction == "bull"
        self.name_tag = name_tag

    def sm_gate_ok(self, df, i: int) -> bool:
        """Stage-0 smart-money gate (spec Section 4). Disabled by default
        (require_sm_alignment: false) -- returns True (no-op) unless a
        challenger config explicitly turns it on. When enabled, this is a
        PRE-CONDITION checked before detect_context/detect_trigger -- it
        never generates an entry on its own (spec Section 3: "SM context
        MUST NOT itself generate entries")."""
        if not self.cfg.get("require_sm_alignment"):
            return True
        from . import sm_context  # local: avoids a hard import cycle at module load
        ctx = sm_context.compute_context(df, i, self.full_cfg)
        return sm_context.sm_gate_ok(ctx, self.direction, self.cfg)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    def scan(self, df: pd.DataFrame, i: int, state: dict) -> dict | None:
        """Advances this setup's FSM by one bar (bar i just closed). Returns
        a ready-to-execute ENTRY signal dict, or None. `state` is this
        setup's own mutable dict, created fresh per (setup, symbol) pair by
        the caller -- never shared across setups or across a second, later
        scan of the same setup on different data."""
        raise NotImplementedError
