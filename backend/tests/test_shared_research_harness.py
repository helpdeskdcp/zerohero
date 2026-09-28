"""data/research/shared_harness/ -- the consolidated backtest library
(candle loading, no-look-ahead levels, trade simulation, real-cost bridging,
reporting) built to replace 4 independently hand-rolled copies of the same
logic across today's strategy backtests. Pure functions on synthetic-but-
labeled-as-such fixture data -- never touches real market data or the live
app/DB (costs_bridge only imports app.institutional_edge.costs, a pure
function module)."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data" / "research"))

from shared_harness import costs_bridge, levels, report, simulate  # noqa: E402


def _bars(rows):
    """rows: list of (timestamp_str, o, h, l, c)."""
    idx = pd.to_datetime([r[0] for r in rows])
    df = pd.DataFrame({"open": [r[1] for r in rows], "high": [r[2] for r in rows],
                       "low": [r[3] for r in rows], "close": [r[4] for r in rows]}, index=idx)
    df.index.name = "datetime"
    return df


# --------------------------------------------------------------------- levels
def test_resample_ohlc_uses_explicit_left_labeling():
    df = _bars([("2026-01-05 09:15", 100, 101, 99, 100),
               ("2026-01-05 09:16", 100, 102, 100, 101)])
    out = levels.resample_ohlc(df, "1D")
    assert out.index[0] == pd.Timestamp("2026-01-05")
    assert out["high"].iloc[0] == 102 and out["low"].iloc[0] == 99


def test_prior_period_value_never_leaks_the_current_period():
    # already daily-granularity, label='left' (each row = that day's own
    # start) -- exactly what resample_ohlc() would produce
    daily = _bars([("2026-01-05", 100, 105, 95, 102), ("2026-01-06", 102, 108, 100, 106)])
    target_index = pd.to_datetime(["2026-01-06 09:20", "2026-01-06 09:25"])
    prior_high = levels.prior_period_value(target_index, daily, "high")
    # both bars on Jan-6 must see Jan-5's high (105), never Jan-6's own (108)
    assert list(prior_high) == [105.0, 105.0]


def test_prior_period_value_is_nan_before_any_prior_period_exists():
    daily = _bars([("2026-01-05", 100, 105, 95, 102)])
    daily = levels.resample_ohlc(daily, "1D")
    target_index = pd.to_datetime(["2026-01-05 09:20"])
    prior_high = levels.prior_period_value(target_index, daily, "high")
    assert pd.isna(prior_high[0])


def test_opening_range_only_visible_after_it_closes():
    df = _bars([("2026-01-05 09:15", 100, 101, 99, 100),
               ("2026-01-05 09:20", 100, 103, 98, 102),  # range high 103 / low 98
               ("2026-01-05 09:29", 102, 102, 96, 97),
               ("2026-01-05 09:31", 97, 99, 96, 98)])
    out = levels.opening_range(df)
    assert pd.isna(out["or_high"].iloc[0])  # 09:15, range not closed yet
    assert out["or_high"].iloc[3] == 103 and out["or_low"].iloc[3] == 96  # 09:31, visible


def test_daily_pivot_cpr_bc_never_exceeds_tc():
    daily = _bars([("2026-01-05", 100, 110, 90, 95)])  # pivot below bc -> would invert without normalization
    out = levels.daily_pivot_cpr(daily)
    assert out["bc"].iloc[0] <= out["tc"].iloc[0]


# ------------------------------------------------------------------- simulate
def test_simulate_trade_long_hits_target():
    bars = _bars([("2026-01-05 09:15", 100, 100, 100, 100),
                  ("2026-01-05 09:16", 100, 106, 99, 105),
                  ("2026-01-05 09:17", 105, 108, 104, 107)])
    t = simulate.simulate_trade(bars, 0, "LONG", entry=100, stop=95, target=105, max_bars=5)
    assert t["reason"] == "TP" and t["exit"] == 105
    assert t["exit_ts"] == bars.index[1]  # the bar where TP actually hit, not entry_ts


def test_simulate_trade_short_hits_stop():
    bars = _bars([("2026-01-05 09:15", 100, 100, 100, 100),
                  ("2026-01-05 09:16", 100, 106, 99, 104)])
    t = simulate.simulate_trade(bars, 0, "SHORT", entry=100, stop=105, target=90, max_bars=5)
    assert t["reason"] == "SL" and t["exit"] == 105


def test_simulate_trade_exits_on_session_change_not_past_it():
    bars = _bars([("2026-01-05 09:15", 100, 100, 100, 100),
                  ("2026-01-05 15:29", 100, 101, 99, 100),
                  ("2026-01-06 09:15", 100, 103, 98, 101)])  # next day -- must not be walked into
    t = simulate.simulate_trade(bars, 0, "LONG", entry=100, stop=90, target=200, max_bars=10)
    assert t["reason"] == "EOD"
    assert t["exit"] == 100  # last SAME-day bar's close (index 1), not day 2's


def test_pnl_points_sign_convention():
    win_long = {"direction": "CE", "entry": 100, "exit": 110}
    loss_long = {"direction": "CE", "entry": 100, "exit": 90}
    win_short = {"direction": "PE", "entry": 100, "exit": 90}
    assert simulate.pnl_points(win_long) == 10
    assert simulate.pnl_points(loss_long) == -10
    assert simulate.pnl_points(win_short) == 10


def test_next_scan_index_skips_past_confirmation_bar_on_fire():
    """The exact fix for reversal_sweep_2026-09's real bug: firing at i=5,
    confirmed at j=8, must resume scanning at 9, not 6 -- otherwise bars
    6/7/8 of the same move can re-fire as "new" signals."""
    assert simulate.next_scan_index(5, fired=True, fired_at=8) == 9
    assert simulate.next_scan_index(5, fired=False, fired_at=None) == 6


def test_apply_daily_limits_stops_after_max_trades_per_day():
    ts = pd.Timestamp("2026-01-05 09:15")
    trades = [{"entry_ts": ts + pd.Timedelta(minutes=i * 10), "reason": "TP"} for i in range(5)]
    kept = simulate.apply_daily_limits(trades, max_per_day=3, max_consecutive_sl=2)
    assert len(kept) == 3


def test_apply_daily_limits_stops_after_consecutive_sl():
    ts = pd.Timestamp("2026-01-05 09:15")
    trades = [
        {"entry_ts": ts, "reason": "SL"},
        {"entry_ts": ts + pd.Timedelta(minutes=10), "reason": "SL"},
        {"entry_ts": ts + pd.Timedelta(minutes=20), "reason": "TP"},  # must be excluded
    ]
    kept = simulate.apply_daily_limits(trades, max_per_day=5, max_consecutive_sl=2)
    assert len(kept) == 2


def test_apply_daily_limits_resets_consecutive_sl_on_a_win():
    ts = pd.Timestamp("2026-01-05 09:15")
    trades = [
        {"entry_ts": ts, "reason": "SL"},
        {"entry_ts": ts + pd.Timedelta(minutes=10), "reason": "TP"},
        {"entry_ts": ts + pd.Timedelta(minutes=20), "reason": "SL"},
        {"entry_ts": ts + pd.Timedelta(minutes=30), "reason": "SL"},
        {"entry_ts": ts + pd.Timedelta(minutes=40), "reason": "TP"},  # would be the 3rd consecutive SL's follower -> excluded
    ]
    kept = simulate.apply_daily_limits(trades, max_per_day=10, max_consecutive_sl=2)
    assert len(kept) == 4


# ---------------------------------------------------------------- costs_bridge
def test_net_pnl_index_option_uses_the_real_formula_estimate_not_ok():
    trade = {"entry_premium": 100.0, "exit_premium": 95.0}
    out = costs_bridge.net_pnl_index_option(trade, lot_size=65)
    assert out["cost_status"] == "FORMULA_ESTIMATE"
    assert out["gross_pnl"] == pytest.approx((95.0 - 100.0) * 65)
    assert out["net_pnl"] == pytest.approx(out["gross_pnl"] - out["cost_total"])


def test_net_pnl_mcx_option_uses_the_real_validated_profile():
    trade = {"entry_premium": 50.0, "exit_premium": 55.0}
    out = costs_bridge.net_pnl_mcx_option(trade, "MCX", "NATURALGAS_OPTION", lot_size=1250)
    assert out["cost_status"] == "OK"
    assert out["cost_total"] == 113.50


# --------------------------------------------------------------------- report
def test_compute_report_insufficient_sample_below_threshold():
    trades = [{"direction": "CE", "entry": 100, "exit": 101}]
    r = report.compute_report(trades, group_by=None)
    assert r["verdict"] == "INSUFFICIENT_SAMPLE"


def test_compute_report_groups_by_tag():
    trades = ([{"direction": "CE", "entry": 100, "exit": 101, "tag": "A"}] * 20
             + [{"direction": "CE", "entry": 100, "exit": 99, "tag": "B"}] * 15)
    r = report.compute_report(trades, group_by="tag")
    assert r["n"] == 35
    assert r["by_tag"]["A"]["win_rate"] == 1.0
    assert r["by_tag"]["B"]["win_rate"] == 0.0


def test_write_verdict_md_produces_a_readable_file(tmp_path):
    out_path = tmp_path / "VERDICT.md"
    report.write_verdict_md(str(out_path), title="Test Rule", rule_description="A test rule.",
                            data_sources=["synthetic fixture data"], disclosed_choices=["none"],
                            sections=[("Results", {"n": 10, "win_rate": 0.5})],
                            bottom_line="NO-GO (synthetic test).")
    text = out_path.read_text()
    assert "# Test Rule" in text and "NO-GO" in text and '"win_rate": 0.5' in text
