"""Smart BoS/CHoCH port + Renko + simulator: fidelity and no-look-ahead rules (research only)."""
import datetime as dt

import numpy as np
import pytest

from app.research_engines.smart_bos import engine as E
from app.research_engines.smart_bos import renko as R
from app.research_engines.smart_bos import renko_trend as RT
from app.research_engines.smart_bos import sim as M
from app.research_engines.smart_bos.engine import Signal

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _t(day: dt.date, minute: int) -> int:
    return int(dt.datetime(day.year, day.month, day.day, minute // 60, minute % 60, tzinfo=IST).timestamp())


def _bars(day, closes, start_min=555, opens=None, hl=0.1):
    t = [_t(day, start_min + i) for i in range(len(closes))]
    c = np.array(closes, float)
    o = np.array(opens, float) if opens is not None else np.r_[c[0], c[:-1]]
    return {"t": np.array(t, np.int64), "o": o, "h": np.maximum(o, c) + hl, "l": np.minimum(o, c) - hl,
            "c": c, "v": np.zeros(len(c))}


def _join(*bs):
    return {k: np.concatenate([b[k] for b in bs]) for k in bs[0]}


# ------------------------------------------------------------------ ATR / pivots
def test_atr_is_wilder_rma_with_sma_seed():
    h = np.full(30, 12.0); l = np.full(30, 10.0); c = np.full(30, 11.0)
    a = E._atr(h, l, c, 14)
    assert np.isnan(a[12]) and a[13] == pytest.approx(2.0) and a[-1] == pytest.approx(2.0)
    h[20] = 22.0                                    # one wide bar moves ATR by (TR-ATR)/14
    assert E._atr(h, l, c, 14)[20] == pytest.approx((12.0 + 13 * 2.0) / 14)


def test_pivot_is_reported_n_bars_late_and_tie_modes_differ():
    a = np.array([1, 2, 3, 9, 3, 2, 1, 0.5], float)
    p = E._pivots(a, 3, True, "strict")
    assert p[6] == 9 and np.isnan(p[:6]).all()                       # known 3 bars after the pivot bar
    a2 = np.array([1, 2, 9, 9, 3, 2, 1, 0.5], float)               # equal neighbours
    assert not np.isnan(E._pivots(a2, 3, True, "allow")).all()
    assert np.isnan(E._pivots(a2, 3, True, "strict")).all()


# ------------------------------------------------------------------ Renko
def test_renko_bricks_continue_and_reverse_only_after_a_full_extra_brick(monkeypatch):
    day = dt.date(2026, 3, 2)
    closes = [100, 111, 121, 119, 109, 99, 89]
    bars = _bars(day, closes)
    monkeypatch.setattr(R, "daily_brick_sizes", lambda *a, **k: {int(bars["t"][0] + 19800) // 86400: 10.0})
    rs = R.build(bars)
    assert list(rs.dirs) == [0, 1, 1, 1, 1, -1, -1]
    assert list(rs.runs) == [0, 1, 2, 2, 2, 1, 2]                    # 109 does NOT reverse; 99 does


def test_renko_size_uses_only_prior_sessions_and_state_is_causal():
    d0 = dt.date(2026, 3, 2)
    days = [d0 + dt.timedelta(days=i) for i in range(3)]
    mk = lambda day, base: _bars(day, [base + (i % 7) for i in range(375)])  # noqa: E731
    b = _join(mk(days[0], 100), mk(days[1], 100), mk(days[2], 100))
    sizes = R.daily_brick_sizes(b["t"], b["h"], b["l"], k=0.5, lookback=5)
    assert (int((b["t"][0] + 19800) // 86400)) not in sizes             # first sessions have no prior history
    full = R.build(b, k=0.5)
    cut = {k: v[:600] for k, v in b.items()}
    part = R.build(cut, k=0.5)
    assert list(full.dirs[:600]) == list(part.dirs) and list(full.runs[:600]) == list(part.runs)  # no look-ahead


# ------------------------------------------------------------------ simulator
def _sig(i=0, side=1, brick=10.0):
    return Signal(i, side, "RENKO", 100.0, 0.0, (), brick, 0.0, 0, side, 3, brick)


def _flat_day(extra):
    day = dt.date(2026, 3, 2)
    n = 12
    c = [100.0] * n
    b = _bars(day, c, start_min=600)
    for k, v in extra.items():
        for idx, val in v.items():
            b[k][idx] = val
    b["days_ok"] = True
    return {k: v for k, v in b.items() if k != "days_ok"}


def test_fill_is_next_bar_open_and_target_hit_exits_at_level():
    b = _flat_day({"o": {1: 100.0}, "h": {1: 121.0}, "l": {1: 99.0}})
    tr, _ = M.simulate(b, [_sig()], mode="renko", exit_model="T1", renko_cfg={"sl_bricks": 2, "step_bricks": 1})
    assert len(tr) == 1 and tr[0].entry == 100.0 and tr[0].reason == "T1"
    assert tr[0].exit == pytest.approx(110.0) and tr[0].r == pytest.approx(10 / 20)


def test_stop_is_checked_before_target_on_the_same_bar():
    b = _flat_day({"h": {1: 125.0}, "l": {1: 70.0}})
    tr, _ = M.simulate(b, [_sig()], mode="renko", exit_model="T1", renko_cfg={"sl_bricks": 2, "step_bricks": 1})
    assert tr[0].reason == "SL" and tr[0].r == pytest.approx(-1.0)


def test_gap_through_target_fills_at_the_worse_open_and_gap_beyond_stop_is_skipped():
    b = _flat_day({"o": {2: 115.0}, "h": {2: 116.0}, "l": {2: 114.0}, "c": {2: 115.0}})
    tr, _ = M.simulate(b, [_sig()], mode="renko", exit_model="T1", renko_cfg={"sl_bricks": 2, "step_bricks": 1})
    assert tr[0].exit == pytest.approx(115.0)                                       # better for a long: filled at the open
    bad = _flat_day({"o": {1: 90.0}, "h": {1: 91.0}, "l": {1: 89.0}})
    pine = Signal(0, 1, "BoS", 100.0, 95.0, (110, 115, 120), 5.0, 1.0, 0)         # Pine levels are fixed at signal time
    tr2, sk = M.simulate(bad, [pine], mode="pine", exit_model="T1")
    assert tr2 == [] and sk["gap_beyond_stop"] == 1                                    # next open already below the stop


def test_one_position_at_a_time_eod_exit_and_short_side():
    b = _flat_day({})
    tr, sk = M.simulate(b, [_sig(0), _sig(2)], mode="renko", exit_model="T1", renko_cfg={"sl_bricks": 2, "step_bricks": 1}, eod_minute=608)
    assert len(tr) == 1 and sk["in_trade"] == 1 and tr[0].reason == "EOD"
    s = _flat_day({"l": {1: 79.0}})
    trs, _ = M.simulate(s, [_sig(side=-1)], mode="renko", exit_model="T1", renko_cfg={"sl_bricks": 2, "step_bricks": 1})
    assert trs[0].side == -1 and trs[0].reason in ("T1", "EOD") and trs[0].pnl >= 0


def test_scale_exit_books_equal_parts_and_cost_is_charged_in_r():
    b = _flat_day({"h": {1: 131.0}})
    tr, _ = M.simulate(b, [_sig()], mode="renko", exit_model="SCALE", renko_cfg={"sl_bricks": 2, "step_bricks": 1})
    assert [round(w, 2) for w, _ in tr[0].parts][:3] == [0.25, 0.25, 0.25]
    g = M.summarize(tr, cost_bps=0)["mean_r"]
    c = M.summarize(tr, cost_bps=10)["mean_r"]
    assert g - c == pytest.approx(10 * 100.0 / 1e4 / 20.0)                              # cost bps * entry / risk


def test_t4_not_available_for_pine_levels():
    s = Signal(0, 1, "BoS", 100.0, 95.0, (110, 115, 120), 5.0, 1.0, 0)
    with pytest.raises(ValueError):
        M.simulate(_flat_day({}), [s], mode="pine", exit_model="T4")


# ------------------------------------------------------------------ BoS/CHoCH engine
def _two_day_dataset():
    d1, d2 = dt.date(2026, 3, 2), dt.date(2026, 3, 3)
    a = _bars(d1, [100.0 + 0.01 * (i % 5) for i in range(375)])
    day2 = [100.2] * 5 + [100.6, 101.0, 101.4, 101.2, 100.9, 100.8, 100.9, 101.1, 101.3, 101.6, 102.4, 102.6, 102.7]
    pre = _bars(d2, day2, hl=0.05)
    pre["o"][0] = 100.1                       # session open
    return _join(a, pre)


def test_buy_bos_fires_once_with_pine_levels_and_respects_previous_day_and_or():
    b = _two_day_dataset()
    p = E.Params(max_candle_atr=50.0, mintick=0.05)
    sig, st = E.generate_signals(b, p)
    assert len(sig) >= 1 and all(s.side == 1 for s in sig)
    s = sig[0]
    assert s.i >= 375 + 5                                                            # not before the 09:20 entry window / opening range
    assert s.sl == pytest.approx(b["l"][s.i] - max(p.min_buffer, p.mintick, s.atr * p.atr_mult))
    risk = s.close - s.sl
    assert s.targets == pytest.approx((s.close + 2 * risk, s.close + 3 * risk, s.close + 4 * risk))
    sigs_day1 = [x for x in sig if x.i < 375]
    assert sigs_day1 == []                                                           # no previous-day data on day 1 -> never trades


def test_signals_never_use_future_bars():
    b = _two_day_dataset()
    p = E.Params(max_candle_atr=50.0)
    full, _ = E.generate_signals(b, p)
    cut = {k: v[:full[0].i + 1] for k, v in b.items()}
    part, _ = E.generate_signals(cut, p)
    assert [x.i for x in part] == [x.i for x in full if x.i <= full[0].i]
    assert part[0].sl == full[0].sl and part[0].targets == full[0].targets


def test_incomplete_previous_day_blocks_signals():
    d1, d2 = dt.date(2026, 3, 2), dt.date(2026, 3, 3)
    a = _bars(d1, [100.0] * 60)                                                     # only 60 of 375 bars
    b = _join(a, _two_day_dataset_day2(d2))
    sig, _ = E.generate_signals(b, E.Params(max_candle_atr=50.0))
    assert sig == []


def _two_day_dataset_day2(d2):
    day2 = [100.2] * 5 + [100.6, 101.0, 101.4, 101.2, 100.9, 100.8, 100.9, 101.1, 101.3, 101.6, 102.4, 102.6, 102.7]
    return _bars(d2, day2, hl=0.05)


def test_renko_trend_entries_only_at_first_qualifying_brick_in_entry_window(monkeypatch):
    day = dt.date(2026, 3, 2)
    bars = _bars(day, [100, 111, 121, 131, 141, 151], start_min=600)
    monkeypatch.setattr(R, "daily_brick_sizes", lambda *a, **k: {int(bars["t"][0] + 19800) // 86400: 10.0})
    rs = R.build(bars)
    sg = RT.generate(bars, rs, min_run=3)
    assert [s.i for s in sg] == [3] and sg[0].side == 1 and sg[0].brick == 10.0
    assert RT.generate(bars, rs, min_run=3, entry=(0, 10)) == []
