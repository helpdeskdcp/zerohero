import numpy as np

from app.strategy_lab import candle_patterns as CP


def bars(rows):
    a = np.array(rows, float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


ATR = None


def pat(rows):
    o, h, l, c = bars(rows)
    return CP.patterns(o, h, l, c, np.full(len(o), 1.0))


def test_bullish_marubozu_needs_a_full_body_bullish_candle():
    p = pat([(10, 10.1, 9.9, 10.0), (10.0, 11.0, 10.0, 11.0)])
    assert p["bull_maru"][1] and not p["bear_maru"][1]
    assert not pat([(10, 10.1, 9.9, 10.0), (10.0, 11.0, 9.8, 10.6)])["bull_maru"][1]        # wicks / small body -> not a marubozu


def test_bearish_marubozu():
    assert pat([(10, 10.1, 9.9, 10.0), (11.0, 11.0, 10.0, 10.0)])["bear_maru"][1]


def test_bullish_engulfing_requires_the_body_to_engulf_the_previous_bearish_body():
    assert pat([(10.5, 10.6, 9.9, 10.0), (9.9, 11.0, 9.8, 10.8)])["bull_engulf"][1]
    assert not pat([(10.5, 10.6, 9.9, 10.0), (10.1, 10.7, 10.0, 10.4)])["bull_engulf"][1]    # does not engulf


def test_bearish_engulfing():
    assert pat([(10.0, 10.6, 9.9, 10.5), (10.6, 10.7, 9.4, 9.6)])["bear_engulf"][1]


def test_bearish_harami_is_a_small_bearish_body_inside_a_large_bullish_body():
    p = pat([(10.0, 11.1, 9.9, 11.0), (10.7, 10.9, 10.3, 10.4)])
    assert p["bear_harami"][1] and not p["bear_engulf"][1]
    assert not pat([(10.0, 11.1, 9.9, 11.0), (11.2, 11.3, 9.5, 9.7)])["bear_harami"][1]       # outside the body -> engulfing, not harami


def test_hammer_and_shooting_star_wicks():
    assert pat([(10, 10.1, 9.9, 10.0), (10.0, 10.1, 9.0, 10.05)])["hammer"][1]
    assert pat([(10, 10.1, 9.9, 10.0), (10.0, 11.0, 9.95, 9.98)])["shooting"][1]


def test_tiny_candles_are_ignored():
    o, h, l, c = bars([(10, 10.1, 9.9, 10.0), (10.0, 10.2, 10.0, 10.2)])
    assert not CP.patterns(o, h, l, c, np.full(2, 5.0))["bull_maru"][1]                       # range 0.2 << 0.6 ATR


def synth(n=400, seed=1):
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0, 0.3, n))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + rng.uniform(0, 0.2, n)
    l = np.minimum(o, c) - rng.uniform(0, 0.2, n)
    day = np.repeat(np.arange(n // 50 + 1), 50)[:n]
    atr = np.full(n, 0.5)
    return o, h, l, c, day, atr


def test_zones_are_causal_future_bars_cannot_change_past_flags():
    o, h, l, c, day, atr = synth()
    z_full = CP.zones(o, h, l, c, atr, day, None)
    cut = 250
    z_pre = CP.zones(o[:cut], h[:cut], l[:cut], c[:cut], atr[:cut], day[:cut], None)
    for k in ("exhaust", "sweep"):
        assert (z_full[k][0][:cut] == z_pre[k][0]).all() and (z_full[k][1][:cut] == z_pre[k][1]).all()
    pdf, pdp = z_full["prevday"][0][:cut], z_pre["prevday"][0]
    assert (pdf[:cut - 50] == pdp[:cut - 50]).all()                                           # previous-day levels only use finished days


def test_bracket_stop_target_gap_and_timeout():
    # entry at open of bar 1 = 100, long, stop 1 -> 99, target 2*1 = 102
    o = np.array([100, 100, 100.5, 100.5, 100.5, 100.5], float)
    h = np.array([101, 100.6, 102.5, 100.6, 100.6, 100.6], float)
    l = np.array([99.5, 99.5, 100.2, 100.4, 100.4, 100.4], float)
    c = np.array([100, 100.2, 102.0, 100.5, 100.5, 100.5], float)
    day = np.zeros(6, int)
    r = CP.bracket(o, h, l, c, day, np.array([0]), np.array([1]), np.array([1.0]), 2.0, max_bars=3)
    assert abs(r[0] - (102 - 100) / 100 * 1e4) < 1e-6                                          # target hit in bar 2
    # stop first when one bar touches both
    h2, l2 = h.copy(), l.copy(); h2[1], l2[1] = 103, 98.5
    r = CP.bracket(o, h2, l2, c, day, np.array([0]), np.array([1]), np.array([1.0]), 2.0, max_bars=3)
    assert abs(r[0] - (99 - 100) / 100 * 1e4) < 1e-6
    # gap through the stop fills at the open, not at the stop
    o3 = o.copy(); o3[2] = 98.0
    r = CP.bracket(o3, h, l, c, day, np.array([0]), np.array([1]), np.array([1.0]), 2.0, max_bars=3)
    assert r[0] < (99 - 100) / 100 * 1e4
    # short mirror
    r = CP.bracket(o, h, l, c, day, np.array([0]), np.array([-1]), np.array([1.0]), 2.0, max_bars=3)
    assert r[0] < 0
