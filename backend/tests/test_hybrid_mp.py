"""Hybrid Market Profile Engine v13 (Pine port): hand-built scenarios mirroring the script's semantics + causality."""
import numpy as np
import pytest

from app.research_engines.hybrid_mp.engine import Params, mtf_dirs, resample, run

T0 = 1_760_000_000 - (1_760_000_000 % 86400) - 19800 + 555 * 60      # 09:15 IST on some day
T0 += (7 - ((T0 + 19800) // 86400 + 3) % 7) % 7 * 86400                 # roll forward to a Monday


def mk(rows, tf=1, start=T0, v=None):
    a = np.array(rows, dtype=float)
    n = len(a)
    return {"t": (start + np.arange(n) * tf * 60).astype(np.int64), "o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3],
            "v": np.zeros(n) if v is None else v}


P = dict(pivot_len=2, atr_len=3, continuation_lookback=3, mintick=0.05)
BASE = [(100, 101, 99, 100), (100, 102, 99.5, 101), (101, 105, 100, 102), (102, 103, 100.5, 101.5),
        (101.5, 102.5, 100, 101), (101, 101.5, 100, 100.5), (100.5, 106, 100.4, 105.8)]          # bar 6 = BoS + continuation


def test_continuation_entry_fires_on_the_bos_bar_itself():
    tr, st = run(mk(BASE + [(105.8, 106, 105, 105.5)]), Params(**P))
    assert st["signals_buy"] == 1 and st["breaks_up"] == 1
    assert tr == [] or tr[0].sig_i == 6                           # trade still open at end -> closed EOD_DATA
    tr, _ = run(mk(BASE + [(105.8, 106, 105, 105.5)]), Params(**P))
    t = tr[0]
    assert (t.sig_i, t.side, t.entry_type) == (6, 1, "CONT") and t.entry == 105.8
    assert t.risk == pytest.approx(5.8)                           # engulf => SL = previous candle low (100)


def test_pine_exit_is_sl_even_after_t1_was_reached_and_stop_first_within_a_bar():
    rows = BASE + [(105.8, 112, 105, 111), (111, 111.5, 99, 100)]  # T1 (111.6) reached on bar 7, SL(100) hit on bar 8
    tr, _ = run(mk(rows), Params(**P))
    t = tr[0]
    assert t.reason == "SL" and t.max_t == 1 and t.r == pytest.approx(-1.0)
    # same-bar T4 and SL: stop wins (adverse-first)
    rows = BASE + [(105.8, 130, 99, 105)]
    tr, _ = run(mk(rows), Params(**P))
    assert tr[0].reason == "SL" and tr[0].max_t == 0


def test_t4_closes_trade_at_plus_4r():
    rows = BASE + [(105.8, 130, 105, 125)]
    tr, _ = run(mk(rows), Params(**P))
    assert tr[0].reason == "T4" and tr[0].r == pytest.approx(4.0) and tr[0].max_t == 4


def test_scale_model_averages_the_targets():
    rows = BASE + [(105.8, 130, 105, 125)]
    tr, _ = run(mk(rows), Params(exit_model="SCALE", **P))
    assert tr[0].r == pytest.approx((1 + 2 + 3 + 4) / 4)


def test_next_open_fill_and_gap_beyond_stop_is_skipped():
    rows = BASE + [(106.5, 112, 106, 111), (111, 130, 110, 125)]
    tr, _ = run(mk(rows), Params(fill="next_open", **P))
    assert tr[0].entry == 106.5                                    # fills at the NEXT bar's open, not the signal close
    rows = BASE + [(99, 99.5, 98, 98.5)]                           # opens below the stop -> never filled
    tr, st = run(mk(rows), Params(fill="next_open", **P))
    assert tr == [] and st["skipped_gap_fill"] == 1


def test_gap_fills_use_the_open_not_the_level():
    rows = BASE + [(98, 98.5, 97, 97.5)]                           # gaps through SL(100)
    tr, _ = run(mk(rows), Params(gap_fills=True, **P))
    assert tr[0].exit == 98 and tr[0].r == pytest.approx((98 - 105.8) / 5.8)
    tr, _ = run(mk(rows), Params(gap_fills=False, **P))
    assert tr[0].exit == 100 and tr[0].r == pytest.approx(-1.0)


def test_retest_only_mode_makes_a_pending_setup_that_expires_without_retest():
    rows = BASE + [(105.8, 107, 105.9 + i * 0.01, 106.5 + i * 0.1) for i in range(1, 12)]
    tr, st = run(mk(rows), Params(entry_mode="retest", **P))
    assert tr == [] and st["pending_set"] == 1 and st["expired"] == 1


def _day(rows_head, n_total, start):
    pad = [(105.5, 105.9, 105.1, 105.5)] * (n_total - len(rows_head))       # quiet bars: no stop/target touched
    return mk(list(rows_head) + pad, start=start)


def test_eod_flat_uses_the_clock_and_flags_missing_session_end_bars():
    head = BASE + [(105.8, 106.2, 105.4, 106.0)]
    # (a) complete 375-bar session: flat at the 15:29 bar's close, reason EOD (decided from the clock, not from bar i+1)
    d1, d2 = _day(head, 375, T0), _day(head, 375, T0 + 86400)
    both = {k: np.concatenate([d1[k], d2[k]]) for k in d1}
    tr, _ = run(both, Params(eod_flat=True, **P))
    assert tr[0].reason == "EOD" and tr[0].exit_i == 374 and tr[0].overnight_days == 0
    assert all(t.reason != "EOD" for t in run(both, Params(eod_flat=False, **P))[0][:1])
    # (b) the day's last bars are missing from the data: the position is NOT silently kept; it is flattened at the next day's
    #     first open and flagged EOD_MISSED (a data-driven "last bar of the day" would have peeked at the next bar)
    d1 = _day(head, 120, T0)
    both = {k: np.concatenate([d1[k], d2[k]]) for k in d1}
    tr, st = run(both, Params(eod_flat=True, **P))
    assert tr[0].reason == "EOD_MISSED" and st["eod_missed"] == 1 and tr[0].exit == d2["o"][0]


def _rw(n, seed):
    r = np.random.default_rng(seed)
    close = 20000 + np.cumsum(r.normal(0, 6, n))
    o = np.r_[close[0], close[:-1]] + r.normal(0, 1, n)
    h = np.maximum(o, close) + np.abs(r.normal(0, 3, n))
    l = np.minimum(o, close) - np.abs(r.normal(0, 3, n))
    return o, h, l, close


def test_prefix_invariance_no_lookahead():
    days = 12
    parts = []
    for d in range(days):
        o, h, l, c = _rw(375, d)
        parts.append(mk(list(zip(o, h, l, c)), start=T0 + d * 86400))
    full = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    cut = 375 * 8 + 120
    pre = {k: v[:cut] for k, v in full.items()}
    for kw in (dict(), dict(fill="next_open", eod_flat=True, gap_fills=True)):
        a, _ = run(full, Params(pivot_len=5, **kw))
        b, _ = run(pre, Params(pivot_len=5, **kw))
        done_a = [(t.sig_i, t.exit_i, round(t.r, 9)) for t in a if t.exit_i < cut - 5]
        done_b = [(t.sig_i, t.exit_i, round(t.r, 9)) for t in b if t.exit_i < cut - 5]
        assert done_a == done_b and len(done_a) > 5


def test_mtf_direction_uses_only_completed_higher_bars():
    parts = []
    for d in range(3):
        o, h, l, c = _rw(375, 100 + d)
        parts.append(mk(list(zip(o, h, l, c)), start=T0 + d * 86400))
    b1 = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    chart = resample(b1, 5)
    p = Params(chart_tf=5, require_mtf=True)
    full = mtf_dirs(chart, b1, p)
    for cut_chart in (40, 100, 200):
        cutoff = chart["t"][cut_chart]                                # chart bar OPEN time
        b1c = {k: v[b1["t"] <= cutoff] for k, v in b1.items()}         # nothing after that bar's first minute exists
        chart_c = {k: v[:cut_chart + 1] for k, v in chart.items()}
        part = mtf_dirs(chart_c, b1c, p)
        assert np.array_equal(part[:cut_chart + 1], full[:cut_chart + 1])


def test_resample_anchors_at_session_open_and_40m_has_a_short_last_bar():
    o, h, l, c = _rw(375, 5)
    b1 = mk(list(zip(o, h, l, c)))
    r40 = resample(b1, 40)
    assert len(r40["t"]) == 10 and r40["t"][0] == b1["t"][0]          # 9 x 40m + one 15m remainder
    assert r40["h"][0] == b1["h"][:40].max() and r40["c"][-1] == b1["c"][-1]


# ======================= fixed version + SMC + dashboard =======================
from app.research_engines.hybrid_mp.engine import fixed_params, vol_usable  # noqa: E402
from app.research_engines.hybrid_mp.smc import SMCParams, smc_context  # noqa: E402


def test_swing_used_flag_stops_repeated_breaks_of_the_same_level():
    rows = BASE + [(105.8, 106.2, 104, 104.5), (104.5, 107, 104.4, 106.9), (106.9, 108, 106, 107.5)]   # re-crosses 105 again
    _, a = run(mk(rows), Params(**P))
    _, b = run(mk(rows), Params(swing_used_flag=True, **P))
    assert a["breaks_up"] >= 2 and b["breaks_up"] == 1


def test_risk_bounds_skip_entries_and_are_counted():
    tr, st = run(mk(BASE + [(105.8, 106, 105, 105.5)]), Params(max_risk_atr=0.5, **P))      # R=5.8 vs ATR ~1.5 -> too wide
    assert tr == [] and st["skipped_risk"] == 1
    tr, st = run(mk(BASE + [(105.8, 106, 105, 105.5)]), Params(min_risk_atr=10.0, **P))
    assert tr == [] and st["skipped_risk"] == 1


def test_entry_window_and_daily_trade_limit():
    _, st = run(mk(BASE), Params(no_entry_first_min=60, **P))                                # bar 6 = 09:21, first hour blocked
    assert st["blocked_window"] == 1 and st["signals_buy"] == 0
    rows = BASE + [(105.8, 112, 105, 111), (111, 111.5, 99, 100)] * 1
    _, st = run(mk(rows), Params(max_trades_day=0, **P))
    assert st["blocked_limits"] >= 1 and st["signals_buy"] == 0


def test_scale_be_moves_stop_to_entry_after_t1():
    rows = BASE + [(105.8, 112, 105, 111), (111, 111.5, 105.0, 105.5)]       # T1 hit bar 7; bar 8 trades back through entry
    tr, _ = run(mk(rows), Params(exit_model="SCALE_BE", **P))
    t = tr[0]
    assert t.reason == "BE" and t.r == pytest.approx(0.25 * 1.0 + 0.75 * 0.0, abs=1e-6)


def test_vwap_proxy_only_when_volume_is_unusable():
    b = mk(BASE)
    assert not vol_usable(b)
    b["v"] = np.full(len(b["t"]), 100.0)
    assert vol_usable(b)


def _smc_bars():
    return mk([(100, 101, 99, 100), (100, 100.5, 99, 99.5), (99.5, 100, 98, 98.5), (98.5, 99, 97.5, 98.8),   # down-move
               (98.8, 103, 98.7, 102.8),                       # bullish displacement
               (102.8, 104, 102.5, 103.5),                      # low 102.5 > high of bar 3?  (99) -> bull FVG between bar 3 high and bar 5 low
               (103.5, 104, 100, 103.8), (103.8, 105, 103, 104.5)])


def test_smc_fvg_displacement_and_discount_detected_causally():
    b = _smc_bars()
    sp = SMCParams(swing_len=1, minor_len=1, atr_len=3, disp_atr=1.0, fvg_min_atr=0.05)
    ctx = smc_context(b, sp)
    assert ctx["disp_bull"][4] and ctx["fvg_new_bull"][5]
    assert ctx["fvg_touch_bull"][6]                          # bar 6 dips into the gap (low 100 <= top 102.5) and closes above bottom
    # prefix invariance: results for the first k bars never change when later bars are appended
    for k in (5, 6, 7):
        part = smc_context({kk: v[:k] for kk, v in b.items()}, sp)
        for key in ("sweep_bull", "sweep_bear", "disp_bull", "fvg_new_bull", "fvg_touch_bull", "bull_score", "bear_score"):
            assert np.array_equal(part[key], ctx[key][:k]), key


def test_smc_equal_lows_and_liquidity_sweep():
    # two equal lows (98) then a wick below them that closes back above -> sell-side sweep (bullish)
    rows = [(100, 101, 99, 100), (100, 100.5, 98, 99), (99, 101, 98.9, 100.5), (100.5, 101.5, 99.5, 101), (101, 101.2, 98.02, 100.2),
            (100.2, 102, 99.8, 101.5), (101.5, 102, 100.5, 101), (101, 101.4, 100.6, 101.2), (101.2, 101.3, 97.5, 100.5), (100.5, 102, 100, 101.8)]
    ctx = smc_context(mk(rows), SMCParams(swing_len=1, minor_len=1, atr_len=3, eq_tol_atr=0.5))
    assert ctx["sweep_bull"][8] and ctx["eq_sweep_bull"][8]


def test_smc_filter_blocks_when_score_too_low_and_dashboard_state_is_complete():
    b = mk(BASE + [(105.8, 106, 105, 105.5)])
    tr, st = run(b, Params(smc_filter=True, smc_min=5, smc=SMCParams(swing_len=2, atr_len=3), **P))
    assert tr == [] and st["blocked_smc"] >= 1
    tr, st = run(b, Params(**P), want_state=True)
    s = st["state"]
    for key in ("mtf", "direction", "trend_status", "trade", "opening_gap", "signal_check", "next_target", "targets", "entry", "sl",
                "levels", "last_result", "planned_qty", "pending", "smc", "vwap", "today"):
        assert key in s, key
    assert s["trade"] == "BUY ACTIVE" and s["next_target"] == "T1" and s["entry"] == 105.8
    assert set(s["smc"]["components"]) == {"sweep", "inducement", "displacement", "fvg_or_ob_touch", "discount_premium"}


def test_fixed_params_prefix_invariance_with_all_filters():
    parts = []
    for d in range(14):
        o, h, l, c = _rw(375, 300 + d)
        parts.append(mk(list(zip(o, h, l, c)), start=T0 + d * 86400))
    b1 = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    full = resample(b1, 5)
    cut = 75 * 9 + 20
    pre = {k: v[:cut] for k, v in full.items()}
    b1_pre = {k: v[b1["t"] <= pre["t"][-1]] for k, v in b1.items()}
    for smc_on in (False, True):
        pp = fixed_params(chart_tf=5, smc_filter=smc_on)
        a, _ = run(full, pp, b1=b1)
        b, _ = run(pre, pp, b1=b1_pre)
        da = [(t.sig_i, t.exit_i, round(t.r, 9)) for t in a if t.exit_i < cut - 3]
        db = [(t.sig_i, t.exit_i, round(t.r, 9)) for t in b if t.exit_i < cut - 3]
        assert da == db


def test_pivot_tie_rule_matches_tradingview_left_ties_allowed_right_ties_not():
    from app.research_engines.hybrid_mp.engine import pivots_tv
    a = np.array([1, 3, 3, 2, 1, 0.0])                     # two equal highs (bars 1 and 2), n=1
    ph = pivots_tv(a, 1, True)
    # pivot candidate bar 2 (value 3): left neighbour 3 (tie, allowed), right 2 -> pivot, known at bar 3
    assert ph[3] == 3 and np.isnan(ph[2])                  # bar 1 is rejected: its RIGHT neighbour ties (3 > 3 is False)
    lo = pivots_tv(np.array([5, 2, 2, 3, 4.0]), 1, False)
    assert lo[3] == 2 and np.isnan(lo[2])


def test_pattern_filter_flag_is_a_real_boolean_not_a_bitwise_accident():
    from app.research_engines.hybrid_mp.engine import _flags
    b = mk([(100, 101, 99, 100.5), (100.5, 102, 100.2, 101.9), (101.9, 103, 101.5, 102.7)])
    on = _flags(b, Params(use_pattern_filter=True))
    off = _flags(b, Params(use_pattern_filter=False))
    assert on["bull_ok"].dtype == bool and off["bull_ok"].dtype == bool
    assert off["bull_ok"].tolist() == (b["c"] > b["o"]).tolist()
    assert (on["bull_ok"] <= off["bull_ok"]).all()          # the pattern requirement can only remove candles
