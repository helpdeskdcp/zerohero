"""Parity-audit regression tests: literal Pine transliteration vs engine, mechanics invariants, ordering, cost model."""
import datetime as dt

import numpy as np
import pytest

from app.research_engines.hybrid_mp.engine import Params, resample, round_trip_cost_bps, run
from app.research_engines.hybrid_mp.reference_literal import run_literal

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
ASIS = dict(fill="close", gap_fills=False, eod_flat=False, exit_model="PINE")


def synth(seed, sessions=8, hole_frac=0.02, start=(2026, 1, 5)):
    r = np.random.default_rng(seed)
    d0 = dt.datetime(*start, 9, 15, tzinfo=IST)
    T, O, H, L, C, V = [], [], [], [], [], []
    px, days = 22000.0, []
    cur = d0
    while len(days) < sessions:
        if cur.weekday() < 5:
            days.append(int(cur.timestamp()))
        cur += dt.timedelta(days=1)
    for base in days:
        px += r.normal(0, 40)
        for m in range(375):
            o = px
            px += r.normal(0, 3.5)
            T.append(base + m * 60); O.append(o); C.append(px)
            H.append(max(o, px) + abs(r.normal(0, 1.5))); L.append(min(o, px) - abs(r.normal(0, 1.5))); V.append(float(r.integers(50, 500)))
    keep = r.random(len(T)) >= hole_frac
    f = lambda x, d=float: np.array(x, dtype=d)[keep]
    return {"t": f(T, np.int64), "o": f(O), "h": f(H), "l": f(L), "c": f(C), "v": f(V)}


def same(a, b, tol=1e-7):
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if len(x) != len(y):
            return False
        for u, v in zip(x, y):
            if isinstance(u, (float, np.floating)) or isinstance(v, (float, np.floating)):
                if abs(float(u) - float(v)) > tol:
                    return False
            elif u != v:
                return False
    return True


CFGS = [
    ({}, {}),
    ({"entryMode": "Breakout + Retest"}, {"entry_mode": "retest"}),
    ({"requireMTF": True}, {"require_mtf": True}),
    ({"useVWAP": True}, {"use_vwap": True}),
    ({"usePatternFilter": True}, {"use_pattern_filter": True}),
    ({"useCandleSL": False}, {"use_candle_sl": False}),
    ({"pivotLen": 3, "continuationLookback": 3, "retestBars": 4}, {"pivot_len": 3, "continuation_lookback": 3, "retest_bars": 4}),
]


@pytest.mark.parametrize("pine_cfg,eng_kw", CFGS)
@pytest.mark.parametrize("tf", [1, 5])
def test_engine_matches_literal_pine_transliteration_event_for_event(pine_cfg, eng_kw, tf):
    b1 = synth(7)                                      # 8 sessions incl. 2% missing candles, overnight gaps, real volume
    chart = b1 if tf == 1 else resample(b1, tf, 555, 930)
    ref = run_literal(chart["t"], chart["o"], chart["h"], chart["l"], chart["c"], chart["v"], b1, dict(pine_cfg, chart_tf=tf))
    _, st = run(chart, Params(chart_tf=tf, **eng_kw, **ASIS), b1=b1, want_events=True)
    assert len(ref) > 30                                # the comparison is not vacuous
    assert same(ref, st["events"]), "first divergence: " + str(next((a, b) for a, b in zip(ref, st["events"]) if not same([a], [b])))


def test_parity_covers_every_weekday_including_friday():
    b1 = synth(3, sessions=10)
    wd = {dt.datetime.fromtimestamp(int(x), IST).weekday() for x in b1["t"]}
    assert wd == {0, 1, 2, 3, 4}
    ref = run_literal(b1["t"], b1["o"], b1["h"], b1["l"], b1["c"], b1["v"], b1, {"chart_tf": 1})
    _, st = run(b1, Params(**ASIS), b1=b1, want_events=True)
    fridays = {i for i in range(len(b1["t"])) if dt.datetime.fromtimestamp(int(b1["t"][i]), IST).weekday() == 4}
    assert any(e[0] in fridays and e[1] == "SIGNAL" for e in st["events"]) and same(ref, st["events"])


@pytest.mark.parametrize("kw", [ASIS, dict(fill="next_open", gap_fills=True, eod_flat=True, exit_model="PINE"),
                                dict(fill="next_open", gap_fills=True, eod_flat=True, exit_model="SCALE_BE"),
                                dict(fill="next_open", gap_fills=True, eod_flat=True, exit_model="SCALE", max_trades_day=3, daily_loss_r=2.0)])
def test_one_position_at_a_time_and_every_trade_has_exactly_one_terminal_exit(kw):
    b = synth(21, sessions=12)
    tr, st = run(b, Params(chart_tf=1, **kw))
    assert len(tr) > 25
    prev_exit = -1
    for t in tr:
        assert t.sig_i > prev_exit, "overlapping positions / entry on the bar that exits"      # signals use the start-of-bar `active` flag
        assert t.exit_i >= t.entry_i >= t.sig_i
        assert abs(sum(w for w, _ in t.parts) - 1.0) < 1e-9, "partial weights must sum to the full position (no duplicate/missing exit)"
        assert t.reason in {"SL", "BE", "T4", "T1", "T2", "T3", "EOD", "EOD_MISSED", "EOD_DATA"}
        prev_exit = t.exit_i
    if kw.get("max_trades_day"):
        from collections import Counter
        assert max(Counter(t.day for t in tr).values()) <= kw["max_trades_day"]


def test_stop_target_ordering_is_conservative_by_default_and_switchable_for_sensitivity():
    from tests.test_hybrid_mp import BASE, P, mk                                   # noqa: E402  (shared hand-built scenario)
    rows = BASE + [(105.8, 130, 99, 105)]                                           # one bar touches SL(100) AND T4(123.0)
    tr, _ = run(mk(rows), Params(**P))
    assert tr[0].reason == "SL" and tr[0].r == pytest.approx(-1.0)                  # OHLC cannot reveal the order -> assume the worst
    tr, _ = run(mk(rows), Params(favorable_first=True, **P))
    assert tr[0].reason == "T4" and tr[0].r == pytest.approx(4.0)                   # sensitivity switch only, never the default


def test_cost_model_components():
    assert round_trip_cost_bps(0.0, 0.0, 0.0) == 0.0
    assert round_trip_cost_bps(commission_bps_per_side=0.5, slippage_bps_per_side=0.5, spread_bps=1.0) == pytest.approx(3.0)


def test_session_gating_rejects_out_of_session_and_weekend_bars_and_duplicates_are_not_created():
    b = synth(5, sessions=3)
    sat = int(dt.datetime(2026, 1, 10, 10, 0, tzinfo=IST).timestamp())             # a Saturday (special session in the real data)
    extra = {k: np.r_[v, np.array([sat + i * 60 for i in range(30)] if k == "t" else [22000.0] * 30)] for k, v in b.items()}
    extra["t"] = extra["t"].astype(np.int64)
    ref = run_literal(extra["t"], extra["o"], extra["h"], extra["l"], extra["c"], extra["v"], extra, {"chart_tf": 1})
    _, st = run(extra, Params(**ASIS), want_events=True)
    assert same(ref, st["events"]) and all(e[0] < len(b["t"]) for e in st["events"] if e[1] == "SIGNAL")        # nothing trades on the Saturday bars
