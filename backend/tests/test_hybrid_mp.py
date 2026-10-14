"""Hybrid Market Profile Engine v13 (Pine port): hand-built scenarios mirroring the script's semantics + causality."""
import numpy as np
import pytest

from app.research_engines.hybrid_mp.engine import Params, mtf_dirs, resample, run

T0 = 1_760_000_000 - (1_760_000_000 % 86400) - 19800 + 555 * 60      # 09:15 IST on some day
T0 += (7 - ((T0 + 19800) // 86400 + 4) % 7) % 7 * 86400                 # roll forward to a Monday


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


def test_eod_flat_closes_on_the_sessions_last_bar():
    rows = BASE + [(105.8, 106.5, 105.2, 106.0)] * 3
    two_days = mk(rows)
    day2 = mk(rows, start=T0 + 86400)
    both = {k: np.concatenate([two_days[k], day2[k]]) for k in two_days}
    # a 7-bar session so bar 9 of day 1 is the last bar of the day: pad by splitting at day boundary
    tr, _ = run(both, Params(eod_flat=True, **P))
    assert any(t.reason == "EOD" for t in tr)
    tr_hold, _ = run(both, Params(eod_flat=False, **P))
    assert all(t.reason != "EOD" for t in tr_hold)


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
