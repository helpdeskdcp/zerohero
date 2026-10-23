"""Smart U-Turn engine tests. ALL data here is SYNTHETIC (clearly labelled) and says nothing about strategy performance."""
import json
import math

import numpy as np
import pandas as pd
import pytest

from app.smart_uturn import backtest as BT
from app.smart_uturn import cli, features_options as FO, indicators as ind, strategy as st
from app.smart_uturn.config import PINE_TF_MAP, BacktestParams, ConfigError, SignalParams
from app.smart_uturn.data import loader as LD, quality as Q, resampler as R, schema as SC
from app.smart_uturn.data import providers as PV

IST = "Asia/Kolkata"


def synth_minutes(days=3, start="2026-03-02", seed=1, vol=2.0, base=24000.0, volume=None) -> pd.DataFrame:
    """SYNTHETIC random-walk NSE 1-minute candles (375/day)."""
    idx = []
    for d in pd.bdate_range(start, periods=days):
        idx += list(pd.date_range(f"{d.date()} 09:15", periods=375, freq="min", tz=IST))
    rng = np.random.default_rng(seed); n = len(idx); c = base + np.cumsum(rng.normal(0, vol, n)); o = np.concatenate([[base], c[:-1]]) + rng.normal(0, 0.3, n)
    h = np.maximum(o, c) + rng.random(n) * vol; l = np.minimum(o, c) - rng.random(n) * vol
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": volume if volume is not None else np.nan}, index=pd.DatetimeIndex(idx))


# ---------------------------------------------------------------- indicators
def test_rma_seed_is_sma_then_recursive():
    out = ind.rma([1, 2, 3, 4, 5, 6], 3)
    assert np.isnan(out[:2]).all() and out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx((4 + 2 * 2.0) / 3) and out[4] == pytest.approx((5 + 2 * out[3]) / 3)


def test_ema_is_seeded_with_first_value():
    out = ind.ema([10, 12, 14], 3)            # alpha 0.5
    assert out[0] == 10 and out[1] == pytest.approx(11) and out[2] == pytest.approx(12.5)


def _rsi_ref(c, n):
    up = [max(c[i] - c[i - 1], 0) for i in range(1, len(c))]; dn = [max(c[i - 1] - c[i], 0) for i in range(1, len(c))]
    out = [math.nan] * len(c); au = sum(up[:n]) / n; ad = sum(dn[:n]) / n
    out[n] = 100.0 if ad == 0 else 0.0 if au == 0 else 100 - 100 / (1 + au / ad)
    for i in range(n + 1, len(c)):
        au = (au * (n - 1) + up[i - 1]) / n; ad = (ad * (n - 1) + dn[i - 1]) / n
        out[i] = 100.0 if ad == 0 else 0.0 if au == 0 else 100 - 100 / (1 + au / ad)
    return np.array(out)


def test_rsi_matches_independent_reference_and_warmup():
    c = 100 + np.cumsum(np.random.default_rng(3).normal(0, 1, 300)); r = ind.rsi(c, 14)
    assert np.isnan(r[:14]).all() and not np.isnan(r[14:]).any()          # first valid at index n; no NaN leaks forward
    assert np.allclose(r[14:], _rsi_ref(c, 14)[14:], atol=1e-9)


def test_rsi_rising_falling_flat_and_short():
    assert ind.rsi(np.arange(1, 40.0), 14)[-1] == 100.0 and ind.rsi(np.arange(40, 1, -1.0), 14)[-1] == 0.0
    assert ind.rsi(np.full(30, 5.0), 14)[-1] == 100.0                      # Pine's own formula: down == 0 -> 100 (documented)
    assert np.isnan(ind.rsi(np.arange(1, 10.0), 14)).all()                 # short history -> NaN, never a guess


def test_indicators_refuse_inner_nan():
    with pytest.raises(ValueError):
        ind.ema([1, 2, np.nan, 4], 3)


def test_atr_first_true_range_is_high_minus_low_and_matches_reference():
    rng = np.random.default_rng(4); c = 100 + np.cumsum(rng.normal(0, 1, 120)); h = c + rng.random(120); l = c - rng.random(120)
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, 120)]
    a = ind.atr(h, l, c, 14); assert np.isnan(a[:13]).all() and a[13] == pytest.approx(np.mean(tr[:14]))
    ref = np.mean(tr[:14]);
    for i in range(14, 120): ref = (ref * 13 + tr[i]) / 14
    assert a[-1] == pytest.approx(ref)


def test_pivot_is_reported_only_after_right_bars_confirm_it():
    h = np.array([1, 2, 3, 10, 3, 2, 1, 1, 1, 1.0]); p = ind.pivot_high(h, 3, 3)
    assert np.isnan(p[:6]).all() and p[6] == 10 and np.isnan(p[7:]).all()  # bar 3 is the peak; known only at bar 6
    assert np.isnan(ind.pivot_high(np.arange(10.0), 3, 3)).all()           # rising series: no pivot
    low = ind.pivot_low(np.array([5, 4, 3, 0, 3, 4, 5.0]), 3, 3); assert low[6] == 0


def test_pivot_tie_rule_documented_equal_tops_both_count():
    h = np.array([1, 2, 5, 5, 2, 1, 0.5, 0.2]); p = ind.pivot_high(h, 2, 2)
    assert p[4] == 5 and p[5] == 5                                         # both equal tops reported (tie rule NOT verified against TradingView)


def test_valuewhen_recent():
    pv = np.array([np.nan, 7, np.nan, 9, np.nan, 11.0])
    assert np.isnan(ind.valuewhen_recent(pv, 0)[0]) and ind.valuewhen_recent(pv, 0)[3] == 9 and ind.valuewhen_recent(pv, 1)[3] == 7 and np.isnan(ind.valuewhen_recent(pv, 2)[3]) and ind.valuewhen_recent(pv, 2)[5] == 7


# ---------------------------------------------------------------- data quality / loading
def test_quality_duplicates_out_of_order_bad_rows_and_gaps_are_counted_not_filled():
    d = synth_minutes(1); d = pd.concat([d, d.iloc[[5]]]).sort_index(kind="stable")                # duplicate timestamp
    d = d.iloc[::-1].copy()                                                                        # fully out of order
    d.iloc[3, d.columns.get_loc("high")] = d.iloc[3]["low"] - 1                                    # inconsistent OHLC
    d = d.drop(d.index[10:13])                                                                     # 3 missing minutes
    out_row = d.iloc[[0]].copy(); out_row.index = out_row.index + pd.Timedelta(hours=8); d = pd.concat([d, out_row])   # outside the session
    clean, rep = Q.validate_one_minute(d)
    assert rep["duplicate_timestamps_dropped"] == 1 and rep["inconsistent_ohlc_dropped"] == 1 and rep["outside_session_rows"] == 1 and rep["out_of_order_rows"] > 0
    assert clean.index.is_monotonic_increasing and clean.index.is_unique
    assert rep["missing_minutes_total"] == 3 + 1 and rep["sessions_complete"] == 0       # 3 dropped + the bad row removed; nothing is filled
    assert rep["fills"].startswith("none")


def test_loader_close_stamped_source_is_shifted_to_open_time():
    raw = pd.DataFrame({"datetime": ["2026-03-02 09:16:00"], "open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5]})
    assert LD.load_dataframe(raw, ts_is="close").index[0] == pd.Timestamp("2026-03-02 09:15", tz=IST)
    assert LD.load_dataframe(raw, ts_is="open").index[0] == pd.Timestamp("2026-03-02 09:16", tz=IST)


def test_feature_availability_never_claims_missing_fields():
    d, _ = Q.validate_one_minute(synth_minutes(1)); a = SC.feature_availability(d)
    assert a["iv"]["status"] == a["oi"]["status"] == a["bid"]["status"] == "UNAVAILABLE" and a["volume"]["status"] == "UNAVAILABLE"
    assert a["_enabled"]["ohlcv_strategy"] and not any(a["_enabled"][k] for k in ("volume_filter", "oi_features", "iv_features", "bid_ask_features"))
    d["volume"] = 0.0; assert SC.feature_availability(d)["volume"]["status"] == "INVALID"      # all-zero index volume is not 'supplied'
    d["iv"] = 18.0; d["bid"] = 10.0; d["ask"] = 10.5; e = SC.feature_availability(d); assert e["_enabled"]["iv_features"] and e["_enabled"]["bid_ask_features"]


# ---------------------------------------------------------------- resampling
@pytest.mark.parametrize("tf,per_day,last_expected", [("3", 125, 3), ("5", 75, 5), ("15", 25, 15), ("30", 13, 15), ("45", 9, 15), ("60", 7, 15)])
def test_intraday_boundaries_anchor_at_session_open_and_never_cross_days(tf, per_day, last_expected):
    d, _ = Q.validate_one_minute(synth_minutes(2)); b = R.resample(d, tf)
    assert len(b) == 2 * per_day and b.complete.all() and b.n_src.sum() == len(d)
    day1 = b[b.index.date == b.index[0].date()]
    assert day1.index[0].strftime("%H:%M") == "09:15" and day1.expected.iloc[-1] == last_expected
    assert day1.close_time.iloc[-1].strftime("%H:%M") == "15:30"          # the short last bar closes at the session end, not later
    assert (b.index.to_series().dt.date == b.close_time.dt.tz_convert(IST).dt.date).all()


def test_ohlc_aggregation_is_exact():
    d, _ = Q.validate_one_minute(synth_minutes(1)); b = R.resample(d, "5"); first = d.iloc[:5]
    r0 = b.iloc[0]; assert (r0.open, r0.high, r0.low, r0.close) == (first.open.iloc[0], first.high.max(), first.low.min(), first.close.iloc[-1])


def test_missing_minute_is_flagged_not_filled_and_missing_bucket_creates_no_candle():
    d, _ = Q.validate_one_minute(synth_minutes(1)); gap = d.drop(d.index[7]); b = R.resample(gap, "5")
    assert not b.iloc[1].complete and b.iloc[1].n_src == 4 and b.iloc[1].expected == 5
    assert b.iloc[1].high == gap.iloc[5:9].high.max()                    # only present minutes
    hole = d.drop(d.index[10:15]); bh = R.resample(hole, "5"); assert len(bh) == 74 and pd.Timestamp("2026-03-02 09:25", tz=IST) not in bh.index


def test_daily_weekly_aggregation_and_in_progress_flags():
    d, _ = Q.validate_one_minute(synth_minutes(7, start="2026-03-02"))        # Mon..Tue next week
    dd = R.resample(d, "D"); w = R.resample(d, "W")
    assert len(dd) == 7 and dd.complete.all() and len(w) == 2 and list(w.complete) == [True, False]      # last week may still be running
    assert w.iloc[0].open == d.iloc[0].open and w.iloc[0].close == d[d.index.isocalendar().week == w.index[0].isocalendar().week].close.iloc[-1]
    q = R.resample(d, "3M"); assert len(q) == 1 and not q.complete.iloc[0]


def test_volume_zero_and_missing_stay_distinct_and_oi_is_a_snapshot():
    d, _ = Q.validate_one_minute(synth_minutes(1)); z = d.assign(volume=0.0); n = d.assign(volume=np.nan)
    assert R.resample(z, "5").volume.iloc[0] == 0.0 and np.isnan(R.resample(n, "5").volume.iloc[0])
    o = d.iloc[:3].assign(oi=[100.0, 110.0, 120.0]); assert R.resample(o, "3").oi.iloc[0] == 120.0         # last snapshot, never the 330 sum


def test_only_supported_timeframes_and_sorted_unique_input():
    d, _ = Q.validate_one_minute(synth_minutes(1))
    with pytest.raises(ValueError): R.resample(d, "7")
    with pytest.raises(ValueError): R.resample(d.iloc[::-1], "5")


# ---------------------------------------------------------------- strategy components
def _flat(n=40):
    idx = pd.date_range("2026-03-02 09:15", periods=n, freq="5min", tz=IST)
    return pd.DataFrame({"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": np.nan}, index=idx)


def test_pine_timeframe_map_is_verbatim():
    assert PINE_TF_MAP == {"1": ("3", "5", "15"), "3": ("5", "30", "45"), "5": ("15", "30", "45"), "15": ("30", "60", "120"), "30": ("45", "60", "120"), "45": ("60", "120", "240"),
                           "60": ("120", "240", "D"), "120": ("240", "D", "W"), "240": ("D", "W", "M"), "D": ("W", "M", "3M"), "W": ("M", "3M", "6M")}


def test_wick_sweep_trend_score_and_pulse_only_on_false_to_true():
    d = _flat(); d.iloc[-1] = [100.0, 100.4, 98.0, 100.3, np.nan]; c = st.turn_components(d, SignalParams())
    assert c["bull_wick"][-1] and c["bull_sweep"][-1] and not c["bull_break"][-1] and c["bull_score"][-1] >= 3 and c["bull_setup"][-1] and c["turn"][-1] == 1 and c["turn"][-2] == 0
    d2 = pd.concat([d, pd.DataFrame([[100.3, 100.7, 97.9, 100.6, np.nan]], columns=d.columns, index=[d.index[-1] + pd.Timedelta(minutes=5)])])
    c2 = st.turn_components(d2, SignalParams()); assert c2["bull_setup"][-1] and c2["turn"][-2] == 1 and c2["turn"][-1] == 0      # consecutive setup bar: no second pulse


def test_bear_mirror_and_volume_filter_refuses_missing_volume():
    d = _flat(); d.iloc[-1] = [100.0, 102.0, 99.6, 99.7, np.nan]; c = st.turn_components(d, SignalParams())
    assert c["bear_wick"][-1] and c["bear_sweep"][-1] and c["turn"][-1] == -1
    with pytest.raises(ConfigError): st.turn_components(d, SignalParams(use_volume=True))              # volume unavailable is not zero


def test_min_score_and_validation_bounds():
    with pytest.raises(ConfigError): SignalParams(min_score=6).validate()
    with pytest.raises(ConfigError): SignalParams(cooldown_bars=-1).validate()
    d = _flat(); d.iloc[-1] = [100.0, 100.4, 98.0, 100.3, np.nan]; assert st.turn_components(d, SignalParams(min_score=5))["turn"][-1] == 0


def test_gate_cooldown_is_per_direction_and_moves_only_on_emitted_signals():
    p = SignalParams(cooldown_bars=3); turn = np.array([1, 0, 0, 1, 0, 1, -1, 0]); mtf = np.zeros(8, int)
    buy, sell, bb, bs = st.gate_signals(turn, mtf, p)
    assert list(buy.nonzero()[0]) == [0, 5] and list(sell.nonzero()[0]) == [6]       # buy@3 is 3 bars after buy@0 -> blocked (needs > 3); buy@5 ok; the sell is independent
    _, _, bb2, _ = st.gate_signals(np.array([1, 1]), np.array([-1, -1]), p); assert bb2.all()


def test_strict_trend_blocks_countertrend_and_only_warns():
    p = SignalParams(); buy, sell, bb, bs = st.gate_signals(np.array([1, -1]), np.array([-1, 1]), p)
    assert not buy.any() and not sell.any() and bb[0] and bs[1]
    buy2, sell2, *_ = st.gate_signals(np.array([1, -1]), np.array([-1, 1]), SignalParams(strict_trend=False)); assert buy2[0] and sell2[1]


def test_mtf_uses_only_the_previous_completed_htf_bar():
    htf = pd.DataFrame(index=pd.date_range("2026-03-02 09:15", periods=3, freq="15min", tz=IST)); series = np.array([1, -1, 1])
    chart = pd.date_range("2026-03-02 09:15", periods=9, freq="5min", tz=IST)
    assert list(st.mtf_values(chart, htf, series)) == [0, 0, 0, 1, 1, 1, -1, -1, -1]
    changed = series.copy(); changed[2] = -1      # the CURRENT/future HTF bar must not matter
    assert list(st.mtf_values(chart, htf, changed)[:6]) == [0, 0, 0, 1, 1, 1]


@pytest.mark.parametrize("chart_tf", ["3", "5", "15"])
def test_no_lookahead_truncating_future_data_changes_no_earlier_signal(chart_tf):
    full, _ = Q.validate_one_minute(synth_minutes(8, seed=11, vol=3.0)); cut = pd.Timestamp("2026-03-06 12:07", tz=IST)
    part = full[full.index < cut]; sp = SignalParams()
    cf, sf = cli.build_signals(full, chart_tf, sp, R.NSE); cp, sp_ = cli.build_signals(part, chart_tf, sp, R.NSE)
    done = cp.index[cp.complete.to_numpy()]
    cols = ["turn", "buy_signal", "sell_signal", "blocked_buy", "blocked_sell", "mtf_dir", "sigA", "sigB", "sigC", "trA", "trB", "trC", "entry", "stop", "t1", "t2", "t3", "active_dir", "support", "resistance"]
    pd.testing.assert_frame_equal(sf.loc[done, cols], sp_.loc[done, cols], check_dtype=False)
    assert sf.buy_signal.sum() + sf.sell_signal.sum() > 0                                       # the comparison is not vacuous


def test_levels_follow_pine_formula():
    d, _ = Q.validate_one_minute(synth_minutes(5, seed=5, vol=3.0)); sp = SignalParams(); chart, sig = cli.build_signals(d, "5", sp, R.NSE)
    new = sig[sig.new_levels]; assert len(new) > 0
    for t, r in new.iloc[:30].iterrows():
        i = chart.index.get_loc(t); lo = chart.low.iloc[max(0, i - 7):i + 1].min(); hi = chart.high.iloc[max(0, i - 7):i + 1].max(); md = max(r.atr * sp.min_risk_atr, sp.mintick * 2)
        e = chart.close.iloc[i]; raw = lo - r.atr * sp.sl_buffer if r.active_dir == 1 else hi + r.atr * sp.sl_buffer
        stop = min(raw, e - md) if r.active_dir == 1 else max(raw, e + md)
        assert r.entry == pytest.approx(e) and r.stop == pytest.approx(stop) and r.risk == pytest.approx(abs(e - stop)) and r.t2 == pytest.approx(e + r.active_dir * 2 * abs(e - stop))
        assert abs(e - r.stop) >= md - 1e-9


def test_dashboard_data_and_structure_state():
    d, _ = Q.validate_one_minute(synth_minutes(5, seed=5, vol=3.0)); _, sig = cli.build_signals(d, "5", SignalParams(), R.NSE)
    dash = st.dashboard(sig); assert dash["tf"] == {"A": "15", "B": "30", "C": "45"} and dash["decision"] in ("BULL TREND", "BEAR TREND", "MIXED / WAIT")
    assert set(sig.structure.unique()) <= {"BULL RETEST", "BEAR RETEST", "BULL BREAKOUT", "BEAR BREAKOUT", "WAIT / RANGE"}


# ---------------------------------------------------------------- backtest (all synthetic, hand-built signals)
def _bars(rows):
    idx = pd.date_range("2026-03-02 09:15", periods=len(rows), freq="5min", tz=IST)
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def _sig(bars, signals, stop=99.0, dir_=1):
    n = len(bars); s = pd.DataFrame({"buy_signal": False, "sell_signal": False, "new_levels": False, "stop": np.nan, "t1": np.nan, "t2": np.nan, "t3": np.nan, "atr": 1.0}, index=bars.index)
    for i in signals:
        s.iloc[i, s.columns.get_loc("buy_signal" if dir_ == 1 else "sell_signal")] = True; s.iloc[i, s.columns.get_loc("new_levels")] = True
        s.iloc[i, s.columns.get_loc("stop")] = stop; s.iloc[i, s.columns.get_loc("t1")] = 100 + dir_ * 1; s.iloc[i, s.columns.get_loc("t2")] = 100 + dir_ * 2; s.iloc[i, s.columns.get_loc("t3")] = 100 + dir_ * 3
    return s


BP0 = BacktestParams(cost_points_roundtrip=0.0)


def test_next_open_entry_and_thirds_accounting():
    b = _bars([(100, 100.2, 99.8, 100), (100.2, 101.5, 100.0, 101.2), (101.2, 102.5, 101, 102.2), (102.2, 103.5, 102, 103.2), (103, 103, 103, 103)])
    r = BT.run_backtest(b, _sig(b, [0]), BP0, SignalParams()); t = r["trades"].iloc[0]
    assert t.entry == 100.2 and t.gross_points == pytest.approx((0.8 + 1.8 + 2.8) / 3) and t.gross_R == pytest.approx(t.gross_points / 1.0) and t.units == 5000   # 500000*1%/R(=1)


def test_signal_close_variant_enters_at_signal_close_but_is_labelled_optimistic():
    b = _bars([(100, 100.2, 99.8, 100), (100.5, 101.5, 100.4, 101.2), (101.2, 102.5, 101, 102.2), (102.2, 103.5, 102, 103.2), (103, 103, 103, 103)])
    t = BT.run_backtest(b, _sig(b, [0]), BacktestParams(entry_mode="SIGNAL_CLOSE", cost_points_roundtrip=0.0), SignalParams())["trades"].iloc[0]
    assert t.entry == 100.0 and t.gross_points == pytest.approx((1 + 2 + 3) / 3)


def test_stop_and_target_in_one_candle_resolves_to_the_stop():
    b = _bars([(100, 100.2, 99.8, 100), (100.2, 101.5, 98.9, 100.0), (100, 100.1, 99.9, 100.0)])
    t = BT.run_backtest(b, _sig(b, [0]), BP0, SignalParams())["trades"].iloc[0]
    assert t.exit_reason == "STOP" and t.gross_points == pytest.approx(99.0 - 100.2) and t.legs.count("T1") == 0


def test_gap_through_the_stop_after_entry_fills_at_the_worse_open():
    b = _bars([(100, 100.2, 99.8, 100), (100.1, 100.5, 99.5, 100.2), (98.0, 98.5, 97.5, 98.0), (98, 98, 98, 98)])
    t = BT.run_backtest(b, _sig(b, [0]), BP0, SignalParams())["trades"].iloc[0]
    assert t.exit_reason == "GAP_STOP" and t.gross_points == pytest.approx(98.0 - 100.1)


def test_entry_gap_through_stop_or_beyond_t1_is_refused_not_traded():
    b = _bars([(100, 100.2, 99.8, 100), (98.0, 98.5, 97.5, 98.0), (98, 98, 98, 98)])
    r = BT.run_backtest(b, _sig(b, [0]), BP0, SignalParams()); assert r["trades"].empty and list(r["rejections"].reason) == ["entry_gap_through_stop"]
    b2 = _bars([(100, 100.2, 99.8, 100), (101.4, 102.0, 101.2, 101.5), (101.5, 101.5, 101.5, 101.5)])
    assert list(BT.run_backtest(b2, _sig(b2, [0]), BP0, SignalParams())["rejections"].reason) == ["entry_gap_beyond_T1"]


def test_session_end_exit_ignore_while_open_and_last_bar_rejection():
    b = _bars([(100, 100.2, 99.8, 100), (100, 100.4, 99.6, 100.1), (100.1, 100.4, 99.6, 100.2), (100.2, 100.4, 99.6, 100.3)])
    sg = _sig(b, [0, 1, 3]); r = BT.run_backtest(b, sg, BP0, SignalParams()); t = r["trades"].iloc[0]
    assert len(r["trades"]) == 1 and t.exit_reason == "SESSION_END" and t.legs.endswith(f"SESSION_END@{b.close.iloc[-1]:.2f}x1.00")
    assert set(r["rejections"].reason) == {"position_open", "no_next_bar_in_session"}


def test_costs_sizing_short_and_repeatability():
    b = _bars([(100, 100.2, 99.8, 100), (99.9, 100.0, 98.4, 98.6), (98.6, 98.7, 96.8, 97.0), (97, 97, 96, 96.5)])
    sg = _sig(b, [0], stop=101.0, dir_=-1); bp = BacktestParams(exit_model="T2", cost_points_roundtrip=0.5)
    r1 = BT.run_backtest(b, sg, bp, SignalParams()); r2 = BT.run_backtest(b, sg, bp, SignalParams()); t = r1["trades"].iloc[0]
    assert t.dir == "SHORT" and t.entry == 99.9 and t.gross_points == pytest.approx(99.9 - 98.0) and t.net_points == pytest.approx(t.gross_points - 0.5) and t.units == 5000
    pd.testing.assert_frame_equal(r1["trades"], r2["trades"]); assert r1["summary"] == r2["summary"]


def test_equity_drawdown_and_zero_trade_honesty():
    b = _bars([(100, 100.2, 99.8, 100), (100, 100.1, 99.9, 100)])
    r = BT.run_backtest(b, _sig(b, []), BP0, SignalParams()); assert r["summary"]["verdict"] == "NO_TRADES" and r["trades"].empty
    b2 = _bars([(100, 100.2, 99.8, 100), (100, 100.2, 98.0, 98.5), (98.5, 98.6, 98.4, 98.5)]); r2 = BT.run_backtest(b2, _sig(b2, [0]), BP0, SignalParams())
    assert r2["summary"]["net_points"] < 0 and r2["summary"]["max_drawdown_rs"] > 0 and r2["summary"]["verdict"].startswith("INSUFFICIENT")


def test_backtest_params_refuse_favourable_collision_rule():
    with pytest.raises(ConfigError): BacktestParams(same_bar_rule="TARGET_FIRST").validate()


# ---------------------------------------------------------------- options features, providers, cli
def test_option_features_are_unavailable_without_real_fields():
    d, _ = Q.validate_one_minute(synth_minutes(1))
    for f in (FO.oi_change, FO.spread, FO.bid_ask_imbalance, FO.iv_change):
        with pytest.raises(FO.Unavailable): f(d)
    o = d.iloc[:4].assign(oi=[100.0, 110.0, 105.0, 120.0], expiry="E1", strike=24000, option_type="CE")
    assert list(FO.oi_change(o).dropna()) == [10.0, -5.0, 15.0] and FO.oi_buildup(o).dropna().isin(["LONG_BUILDUP", "SHORT_BUILDUP", "SHORT_COVERING", "LONG_UNWINDING"]).all()
    with pytest.raises(FO.Unavailable): FO.oi_change(o.assign(strike=[24000, 24050, 24000, 24000]))     # mixed strikes are refused
    q = d.iloc[:2].assign(bid=[10.0, 0.0], ask=[10.5, 11.0]); s = FO.spread(q); assert s.iloc[0] == pytest.approx(0.5) and np.isnan(s.iloc[1])    # zero bid is not a quote


class _P(PV.HistoricalProvider):
    def __init__(self, name, df=None, err=None): self.name, self.df, self.err, self.calls = name, df, err, 0

    def fetch_1m(self, symbol, start, end):
        self.calls += 1
        if self.err: raise self.err
        return self.df


def test_provider_fallback_backoff_and_no_invented_success():
    good = synth_minutes(1); sleeps = []
    a, b = _P("primary", err=RuntimeError("down")), _P("fallback", df=good)
    d, info = PV.fetch_with_fallback([a, b], "NIFTY", "2026-03-02", "2026-03-03", sleep=sleeps.append)
    assert info["used"] == "fallback" and a.calls == 4 and len(d) == 375 and max(sleeps) <= 8.0 and (d.source == "fallback").all() and info["health"][0]["ok"] is False
    with pytest.raises(PV.ProviderError): PV.fetch_with_fallback([_P("x", err=RuntimeError("down")), _P("y", df=good.iloc[0:0])], "NIFTY", "a", "b", sleep=lambda s: None)


def test_cli_validate_resample_signals_backtest_on_synthetic_csv(tmp_path):
    d = synth_minutes(4, seed=2, vol=3.0); csv = tmp_path / "one_minute.csv"; d.reset_index().rename(columns={"index": "datetime"}).assign(datetime=lambda x: x.datetime.dt.strftime("%Y-%m-%d %H:%M:%S%z")).to_csv(csv, index=False)
    out = str(tmp_path / "out")
    assert cli.main(["validate", "--input", str(csv), "--out", out]) == 0
    assert cli.main(["resample", "--input", str(csv), "--out", out, "--tfs", "1,3,5,15,30,45,D,W"]) == 0
    assert cli.main(["signals", "--input", str(csv), "--out", out, "--chart-tf", "5"]) == 0
    assert cli.main(["backtest", "--input", str(csv), "--out", out, "--chart-tf", "5"]) == 0
    meta = json.load(open(tmp_path / "out" / "run_metadata.json")); assert meta["engine"].startswith("smart_u_turn_py") and (tmp_path / "out" / "report.md").exists()
    assert (tmp_path / "out" / "trades.csv").exists() and (tmp_path / "out" / "feature_availability.json").exists()
