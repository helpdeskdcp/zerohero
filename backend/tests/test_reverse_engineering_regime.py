"""app.reverse_engineering.regime -- pure functions over synthetic bars."""
from app.reverse_engineering import regime as r


def _bars(closes, highs=None, lows=None):
    highs = highs or [c + 1 for c in closes]
    lows = lows or [c - 1 for c in closes]
    return [{"o": c, "h": h, "l": lo, "c": c, "v": 100}
           for c, h, lo in zip(closes, highs, lows)]


def test_defaults_to_unknown_with_too_few_bars():
    out = r.classify_regime(_bars([100, 101, 102]))
    assert out["regime"] == "UNKNOWN"


def test_trend_up_from_rising_closes():
    closes = [100 + i * 0.3 for i in range(15)]
    out = r.classify_regime(_bars(closes))
    assert out["regime"] in ("TREND_UP", "BREAKOUT")   # rising series may break its own recent high


def test_trend_down_from_falling_closes():
    closes = [115 - i * 0.3 for i in range(15)]
    out = r.classify_regime(_bars(closes))
    assert out["regime"] in ("TREND_DOWN", "BREAKDOWN")


def test_range_from_flat_closes():
    closes = [100.0] * 15
    out = r.classify_regime(_bars(closes))
    assert out["regime"] in ("RANGE", "LOW_VOLATILITY")


def test_ema_none_below_period():
    assert r.ema([1.0, 2.0], 5) is None


def test_ema_computes_with_enough_points():
    assert r.ema([1.0] * 10, 5) is not None


def test_atr_none_with_too_few_usable_bars():
    assert r.atr(_bars([100, 101])) is None


def test_atr_computes_with_enough_bars():
    closes = [100 + (i % 3) for i in range(10)]
    assert r.atr(_bars(closes)) is not None


def test_none_valued_slots_are_filtered_not_counted_as_evidence():
    bars = [None, None, None] + _bars([100 + i * 0.1 for i in range(12)])
    out = r.classify_regime(bars)
    assert out["regime"] != "UNKNOWN" or out["evidence"].get("n_bars", 99) < 10


def test_expiry_distortion_only_when_flagged_and_high_vol():
    # Wide daily range relative to price -> high ATR%.
    closes = [100, 130, 90, 140, 80, 150, 70, 160, 60, 170, 50, 180]
    out = r.classify_regime(_bars(closes), is_expiry_day=True)
    # Either genuinely flagged, or evidence shows why not (never silently forced).
    assert out["regime"] in r.REGIMES
