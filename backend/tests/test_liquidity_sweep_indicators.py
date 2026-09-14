"""app/liquidity_sweep/indicators.py -- VWAP/EMA/RSI/MACD/ADX/ATR snapshot."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.liquidity_sweep.indicators import session_vwap, snapshot  # noqa: E402


def _bar(t, o, h, l, c, v=1000):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def test_session_vwap_only_uses_bars_from_the_last_session():
    day1 = [_bar("2026-09-01T09:15:00Z", 100, 101, 99, 100, 1000)]
    day2 = [_bar("2026-09-02T09:15:00Z", 200, 201, 199, 200, 1000)]
    v = session_vwap(day1 + day2)
    assert 195 < v < 205   # anchored to day2 only, not blended with day1's ~100 prices


def test_snapshot_reports_no_bars_status_on_empty_input():
    assert snapshot([])["status"] == "NO_BARS"


def test_snapshot_returns_none_for_indicators_needing_more_history_than_available():
    bars = [_bar(f"t{i}", 100, 101, 99, 100) for i in range(5)]
    out = snapshot(bars)
    assert out["status"] == "OK"
    assert out["ema20"] is None    # needs 20 bars, only 5 given
    assert out["rsi14"] is None
    assert out["atr14"] is None


def test_snapshot_computes_full_indicator_set_with_enough_history():
    bars = [_bar(f"2026-09-01T{9 + i // 12:02d}:{(i % 12) * 5:02d}:00Z",
                100 + i * 0.1, 100 + i * 0.1 + 0.5, 100 + i * 0.1 - 0.5, 100 + i * 0.1 + 0.2, 1000)
            for i in range(60)]
    out = snapshot(bars)
    assert out["status"] == "OK"
    assert out["ema20"] is not None
    assert out["rsi14"] is not None
    assert out["atr14"] is not None
    assert out["adx"] is not None
    assert out["vwap"] is not None
    assert isinstance(out["above_vwap"], bool)


def test_above_vwap_and_above_ema_flags_are_consistent_with_close():
    bars = [_bar(f"2026-09-01T{9 + i // 12:02d}:{(i % 12) * 5:02d}:00Z",
                100, 101, 99, 100 + i, 1000) for i in range(30)]   # steadily rising close
    out = snapshot(bars)
    assert out["above_vwap"] is True
    assert out["above_ema20"] is True
