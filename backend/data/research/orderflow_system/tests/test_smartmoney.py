"""Unit tests for strategy/smartmoney/* detectors + sm_context.py's
aggregator, on small hand-crafted synthetic data. Includes the spec's
explicit requirement: Mode C (proxy) detectors must set is_proxy=True and
the context engine must propagate that into is_proxy on the aggregate."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
import pytest

from config_loader import load_config
from strategy import sm_context
from strategy.smartmoney import (absorption, divergence, fvg, liquidity_sweep,
                                 order_flow_imbalance, structure_shift, value_migration)

CFG = load_config("config/v1.yaml")
SM_CFG = CFG["smartmoney"]


def _df(rows, tz="Asia/Kolkata"):
    idx = pd.date_range("2026-01-05 09:15", periods=len(rows), freq="5min", tz=tz)
    df = pd.DataFrame(rows, index=idx)
    for col, default in [("delta", 0.0), ("cvd", 0.0), ("is_absorption", False),
                         ("atr", 10.0), ("poc", 100.0), ("htf_trend", "bullish")]:
        if col not in df.columns:
            df[col] = default
    return df


# ---------------------------------------------------------------------------
# SM-6 FVG -- real, not proxy
# ---------------------------------------------------------------------------

def test_fvg_detects_a_real_bullish_gap_and_is_not_proxy():
    rows = [{"open": 100, "high": 101, "low": 99, "close": 100.5},   # c1
           {"open": 105, "high": 110, "low": 104, "close": 109},    # c2, impulse
           {"open": 112, "high": 113, "low": 111, "close": 112.5}]  # c3 -- low(111) > high(101) c1
    df = _df(rows)
    r = fvg.detect(df, 2, SM_CFG)
    assert r.present and r.direction == "bull" and r.is_proxy is False


def test_fvg_absent_when_candles_overlap():
    rows = [{"open": 100, "high": 105, "low": 99, "close": 100.5},
           {"open": 105, "high": 106, "low": 104, "close": 105.5},
           {"open": 103, "high": 104, "low": 102, "close": 103.5}]  # overlaps c1's range
    df = _df(rows)
    r = fvg.detect(df, 2, SM_CFG)
    assert r.present is False


def test_fvg_too_small_relative_to_atr_is_ignored():
    rows = [{"open": 100, "high": 100.01, "low": 99.99, "close": 100.0, "atr": 10.0},
           {"open": 100.02, "high": 100.05, "low": 100.01, "close": 100.03, "atr": 10.0},
           {"open": 100.06, "high": 100.08, "low": 100.02, "close": 100.05, "atr": 10.0}]
    df = _df(rows)
    r = fvg.detect(df, 2, SM_CFG)
    assert r.present is False  # gap exists but is tiny noise relative to ATR=10


# ---------------------------------------------------------------------------
# SM-7 Liquidity Sweep -- real, not proxy
# ---------------------------------------------------------------------------

def test_sweep_detects_swept_lows():
    lb = SM_CFG["sweep_swing_lookback"]
    rows = [{"low": 100.0, "high": 105.0, "close": 102.0}] * (lb + 1)
    rows.append({"low": 95.0, "high": 101.0, "close": 100.5})  # wicks below, closes back inside
    df = _df(rows)
    r = liquidity_sweep.detect(df, lb + 1, SM_CFG)
    assert r.present and r.direction == "bull" and r.detail["event"] == "swept_lows"


def test_sweep_absent_when_no_reclaim():
    rows = [{"low": 100.0, "high": 105.0, "close": 102.0}] * 20
    rows.append({"low": 95.0, "high": 96.0, "close": 95.5})  # broke down and stayed there
    df = _df(rows)
    r = liquidity_sweep.detect(df, 20, SM_CFG)
    assert r.present is False


# ---------------------------------------------------------------------------
# SM-8 Structure Shift -- precompute_swings + BOS/CHoCH classification
# ---------------------------------------------------------------------------

def test_bos_when_trend_agrees_choch_when_it_does_not():
    lb = SM_CFG["structure_swing_lookback"]
    rows = [{"high": 100.0, "low": 90.0, "close": 95.0, "htf_trend": "bullish"}] * (4 * lb)
    df = _df(rows)
    df = structure_shift.precompute_swings(df, SM_CFG)
    df.loc[df.index[2 * lb], "high"] = 120.0  # a clear pivot high
    df = structure_shift.precompute_swings(df, SM_CFG)  # recompute after the edit
    breakout_i = 2 * lb + lb + 5
    df.loc[df.index[breakout_i], "close"] = 130.0
    df.loc[df.index[breakout_i], "htf_trend"] = "bullish"
    r_bos = structure_shift.detect(df, breakout_i, SM_CFG)
    assert r_bos.present and r_bos.direction == "bull" and r_bos.detail["event"] == "BOS"

    df.loc[df.index[breakout_i], "htf_trend"] = "bearish"
    r_choch = structure_shift.detect(df, breakout_i, SM_CFG)
    assert r_choch.present and r_choch.detail["event"] == "CHoCH"


# ---------------------------------------------------------------------------
# SM-1/2/3/9 -- proxy detectors MUST report is_proxy=True (spec D1's explicit requirement)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod,setup_row", [
    (order_flow_imbalance, {"delta": 100.0}),
    (absorption, {"is_absorption": True, "high": 105, "low": 100, "close": 104}),
    (divergence, {"close": 200.0, "cvd": -1.0}),
    (value_migration, {"poc": 150.0}),
])
def test_proxy_detectors_always_report_is_proxy_true(mod, setup_row):
    rows = [{"delta": 1.0, "cvd": -10.0, "close": 100.0 + i, "poc": 100.0,
            "high": 100.0 + i + 1, "low": 100.0 + i - 1, "atr": 5.0} for i in range(25)]
    rows[-1].update(setup_row)
    df = _df(rows)
    r = mod.detect(df, len(df) - 1, SM_CFG)
    assert r.is_proxy is True  # regardless of present/absent -- these modes are NEVER "real"


def test_absorption_wraps_the_real_is_absorption_column_not_a_second_definition():
    rows = [{"is_absorption": False}] * 5
    df = _df(rows)
    r = absorption.detect(df, 4, SM_CFG)
    assert r.present is False  # is_absorption column says False -- absorption.py must agree, not recompute


# ---------------------------------------------------------------------------
# sm_context.py aggregator
# ---------------------------------------------------------------------------

def test_compute_context_is_proxy_true_when_any_contributing_detector_is_proxy():
    rows = [{"delta": 1.0, "cvd": -10.0, "close": 100.0 + i, "poc": 100.0,
            "high": 100.0 + i + 1, "low": 100.0 + i - 1, "atr": 5.0} for i in range(30)]
    df = _df(rows)
    ctx = sm_context.compute_context(df, 29, CFG)
    assert ctx.is_proxy is True  # ofi/absorption/divergence/value_migration are always proxy


def test_compute_context_neutral_when_nothing_fires():
    rows = [{"high": 100.0, "low": 99.0, "close": 99.5, "delta": 0.0, "cvd": 0.0,
            "is_absorption": False, "atr": 1.0, "poc": 100.0}] * 30
    df = _df(rows)
    df = structure_shift.precompute_swings(df, CFG["smartmoney"])
    ctx = sm_context.compute_context(df, 29, CFG)
    assert ctx.side == "neutral" and ctx.bias_score == 0.0


def test_sm_gate_ok_requires_matching_side_and_min_bias():
    ctx_long = sm_context.SMContext(side="long", phase="markup", events=[], bias_score=0.8, is_proxy=True)
    setup_cfg = {"sm_min_bias": 0.5, "sm_required_events": []}
    assert sm_context.sm_gate_ok(ctx_long, "bull", setup_cfg) is True
    assert sm_context.sm_gate_ok(ctx_long, "bear", setup_cfg) is False  # wrong side

    ctx_weak = sm_context.SMContext(side="long", phase="unknown", events=[], bias_score=0.2, is_proxy=True)
    assert sm_context.sm_gate_ok(ctx_weak, "bull", setup_cfg) is False  # below sm_min_bias


def test_sm_gate_ok_requires_all_listed_events_present():
    ctx = sm_context.SMContext(side="long", phase="markup",
                               events=[{"detector": "liquidity_sweep"}], bias_score=0.9, is_proxy=True)
    cfg_needs_two = {"sm_min_bias": 0.0, "sm_required_events": ["liquidity_sweep", "structure_shift"]}
    assert sm_context.sm_gate_ok(ctx, "bull", cfg_needs_two) is False  # structure_shift didn't fire
    cfg_needs_one = {"sm_min_bias": 0.0, "sm_required_events": ["liquidity_sweep"]}
    assert sm_context.sm_gate_ok(ctx, "bull", cfg_needs_one) is True
