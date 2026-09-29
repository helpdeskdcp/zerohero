"""Unit tests for the strategy layer's signal math + risk engine, on small
hand-crafted synthetic data -- deterministic, fast, isolate one mechanism
at a time (the real-data tests in test_no_lookahead.py already exercise
the full pipeline end to end)."""
from __future__ import annotations

import sys
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
import pytest

from config_loader import load_config
from strategy._breakout import BreakoutSetup
from strategy.risk import RiskState, apply_partial_booking, position_size

BASE_CFG = load_config("config/v1.yaml")


# ---------------------------------------------------------------------------
# BreakoutSetup._build_entry -- stop/target math, both directions
# ---------------------------------------------------------------------------

def _tiny_df(rows):
    idx = pd.date_range("2026-01-05 09:15", periods=len(rows), freq="5min", tz="Asia/Kolkata")
    return pd.DataFrame(rows, index=idx)


def test_build_entry_long_stop_below_and_targets_above():
    s = BreakoutSetup(BASE_CFG, "setup1_bull_breakout", "bull", "T")
    df = _tiny_df([{"low": 100.0, "high": 101.0}, {"low": 102.0, "high": 103.0}])
    entry = s._build_entry(df, 1, 103.0)
    assert entry["direction"] == "LONG"
    assert entry["stop"] == 100.0 - s.cfg["stop_buffer_ticks"]
    r = entry["entry"] - entry["stop"]
    rr = s.cfg["rr_targets"]
    assert entry["targets"][0] == pytest.approx(entry["entry"] + rr[0] * r)
    assert entry["targets"][1] == pytest.approx(entry["entry"] + rr[1] * r)
    assert entry["targets"][1] > entry["targets"][0]  # tp2 further than tp1, same direction


def test_build_entry_short_stop_above_and_targets_below():
    s = BreakoutSetup(BASE_CFG, "setup2_bear_breakout", "bear", "T")
    df = _tiny_df([{"low": 100.0, "high": 101.0}, {"low": 98.0, "high": 99.0}])
    entry = s._build_entry(df, 1, 98.0)
    assert entry["direction"] == "SHORT"
    assert entry["stop"] == 101.0 + s.cfg["stop_buffer_ticks"]
    r = entry["stop"] - entry["entry"]
    rr = s.cfg["rr_targets"]
    assert entry["targets"][0] == pytest.approx(entry["entry"] - rr[0] * r)
    assert entry["targets"][1] < entry["targets"][0]  # tp2 further down than tp1


# ---------------------------------------------------------------------------
# BreakoutSetup.invalidated -- fakeout / bear-trap wick logic
# ---------------------------------------------------------------------------

def test_invalidated_when_price_closes_back_inside_range_within_fakeout_bars():
    s = BreakoutSetup(BASE_CFG, "setup1_bull_breakout", "bull", "T")
    df = _tiny_df([{"close": 105.0}, {"close": 99.0}])  # closed back below the level (100)
    assert s.invalidated(df, 1, trigger_i=0, level=100.0) is True


def test_not_invalidated_once_fakeout_window_has_passed():
    s = BreakoutSetup(BASE_CFG, "setup1_bull_breakout", "bull", "T")
    df = _tiny_df([{"close": 105.0}] * 10)
    fakeout_bars = s.cfg["fakeout_bars"]
    assert s.invalidated(df, fakeout_bars + 2, trigger_i=0, level=999.0) is False


def test_bear_trap_long_wick_invalidates_a_breakdown():
    s = BreakoutSetup(BASE_CFG, "setup2_bear_breakout", "bear", "T")
    # long lower wick relative to range -- classic bear-trap reversal candle
    df = _tiny_df([{"close": 90.0}, {"low": 80.0, "high": 92.0, "close": 91.0}])
    assert s.invalidated(df, 1, trigger_i=0, level=999999.0) is True  # level far away -- only wick triggers this


# ---------------------------------------------------------------------------
# Risk engine
# ---------------------------------------------------------------------------

def test_position_size_scales_inversely_with_r_points():
    tight = position_size(1_000_000, 0.005, r_points=5.0, lot_size=65)
    wide = position_size(1_000_000, 0.005, r_points=50.0, lot_size=65)
    assert tight > wide > 0


def test_position_size_zero_for_degenerate_stop():
    assert position_size(1_000_000, 0.005, r_points=0.0, lot_size=65) == 0


def test_risk_state_session_blackout_blocks_entries_right_at_open():
    cfg = {"news_blackout_mins": 15, "max_concurrent_positions": 5, "max_daily_loss_r": 3.0}
    rs = RiskState(cfg)
    ts_at_open = datetime(2026, 1, 5, 9, 16)
    assert rs.can_enter(ts_at_open, session_open=time(9, 15)) is False
    ts_after_blackout = datetime(2026, 1, 5, 9, 31)
    assert rs.can_enter(ts_after_blackout, session_open=time(9, 15)) is True


def test_risk_state_blocks_after_max_daily_loss():
    cfg = {"news_blackout_mins": 0, "max_concurrent_positions": 5, "max_daily_loss_r": 2.0}
    rs = RiskState(cfg)
    ts = datetime(2026, 1, 5, 10, 0)
    assert rs.can_enter(ts) is True
    rs.on_entry()
    rs.on_exit(ts, r_multiple=-2.5)  # single loss already exceeds max_daily_loss_r
    assert rs.can_enter(ts + timedelta(minutes=5)) is False


def test_risk_state_daily_loss_resets_on_a_new_day():
    cfg = {"news_blackout_mins": 0, "max_concurrent_positions": 5, "max_daily_loss_r": 2.0}
    rs = RiskState(cfg)
    day1 = datetime(2026, 1, 5, 10, 0)
    rs.on_entry()
    rs.on_exit(day1, r_multiple=-2.5)
    assert rs.can_enter(day1) is False
    day2 = datetime(2026, 1, 6, 10, 0)
    assert rs.can_enter(day2) is True


def test_risk_state_blocks_beyond_max_concurrent_positions():
    cfg = {"news_blackout_mins": 0, "max_concurrent_positions": 2, "max_daily_loss_r": 100.0}
    rs = RiskState(cfg)
    ts = datetime(2026, 1, 5, 10, 0)
    rs.on_entry(); rs.on_entry()
    assert rs.can_enter(ts) is False
    rs.on_exit(ts, r_multiple=1.0)
    assert rs.can_enter(ts) is True


def test_apply_partial_booking_shape():
    plan = apply_partial_booking(100.0, 95.0, [110.0, 115.0], "LONG",
                                 {"partial_pct": 0.5, "move_stop_to_breakeven_on_partial": True})
    assert plan["tp1"] == 110.0 and plan["tp1_fraction"] == 0.5
    assert plan["tp2"] == 115.0 and plan["tp2_fraction"] == 0.5
    assert plan["breakeven_after_tp1"] is True and plan["breakeven_price"] == 100.0


# ---------------------------------------------------------------------------
# V-a structural variant: min_confirmations score-based confirm()
# ---------------------------------------------------------------------------

def test_confirm_is_strict_and_when_min_confirmations_is_unset():
    import copy
    cfg = copy.deepcopy(BASE_CFG)
    assert cfg["setup1_bull_breakout"]["min_confirmations"] is None
    s = BreakoutSetup(cfg, "setup1_bull_breakout", "bull", "T")
    # monkeypatch confirm_breakdown to a known 3-of-4-true breakdown
    s.confirm_breakdown = lambda df, i: {"delta_ok": True, "cvd_ok": True, "absorption_ok": True, "vol_ok": False}
    assert s.confirm(None, 0) is False  # strict AND -- 3/4 is not enough


def test_confirm_score_based_when_min_confirmations_set():
    import copy
    cfg = copy.deepcopy(BASE_CFG)
    cfg["setup1_bull_breakout"]["min_confirmations"] = 3
    s = BreakoutSetup(cfg, "setup1_bull_breakout", "bull", "T")
    s.confirm_breakdown = lambda df, i: {"delta_ok": True, "cvd_ok": True, "absorption_ok": True, "vol_ok": False}
    assert s.confirm(None, 0) is True  # 3/4 >= min_confirmations=3


def test_confirm_score_based_rejects_below_threshold():
    import copy
    cfg = copy.deepcopy(BASE_CFG)
    cfg["setup1_bull_breakout"]["min_confirmations"] = 3
    s = BreakoutSetup(cfg, "setup1_bull_breakout", "bull", "T")
    s.confirm_breakdown = lambda df, i: {"delta_ok": True, "cvd_ok": False, "absorption_ok": True, "vol_ok": False}
    assert s.confirm(None, 0) is False  # only 2/4
