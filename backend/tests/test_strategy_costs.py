from app.strategy_lab import costs as C


def test_nifty_futures_round_trip_is_about_six_bps_after_the_2026_stt_change():
    r = C.futures_round_trip_bps(24000 * 65)
    assert abs(r["stt_bps"] - 5.0) < 1e-9                                  # 0.05% of the sell value
    assert 5.4 < r["statutory_bps"] + r["brokerage_bps"] < 6.4
    assert 5.7 < r["total_bps"] < 6.7 and r["slippage_bps"] == 0.3


def test_execution_controllable_part_is_tiny_versus_statutory():
    r = C.futures_round_trip_bps(24000 * 65)
    tick_bps = 0.10 / 24000 * 1e4                                          # NIFTY futures tick = 0.10 index points
    assert 2 * tick_bps < 0.2 and r["stt_bps"] > 20 * 2 * tick_bps         # even 2 ticks per leg is <0.2 bps: limit orders cannot touch the 5 bps STT


def test_bigger_notional_dilutes_only_the_flat_brokerage():
    small, big = C.futures_round_trip_bps(1_000_000), C.futures_round_trip_bps(10_000_000)
    assert big["brokerage_bps"] < small["brokerage_bps"] / 5 and abs(big["stt_bps"] - small["stt_bps"]) < 1e-9


def test_option_buying_cost_uses_the_higher_stt_on_the_sold_premium():
    c = C.option_buy_round_trip_points(100.0, 120.0, 65)
    assert 1.0 < c < 2.5
    assert abs((C.option_buy_round_trip_points(100.0, 120.0, 65, slippage_pts_per_leg=0.5) - c) - 0.5) < 1e-9
