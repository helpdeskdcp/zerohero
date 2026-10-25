from app.option_selling.backtest import cost, stt_rate


def test_cost_matches_hand_calc():
    c = cost("2026-06-01", [50, 50], [20, 20], [2, 2], [10, 10], 65)
    sell = 104 * 65; buy = 60 * 65; tot = sell + buy
    ex = 0.000355299 * tot + 0.000001 * tot
    assert abs(c - (160 + ex + 0.0015 * sell + 0.00003 * buy + 0.18 * (160 + ex))) < 0.01


def test_stt_regime_switch():
    assert stt_rate("2026-03-31") == 0.001 and stt_rate("2026-04-01") == 0.0015
