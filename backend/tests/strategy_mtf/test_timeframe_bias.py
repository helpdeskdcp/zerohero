"""
Per-timeframe 5-vs-20 SMA bias, and the anti-repaint contract at this
layer: a caller must get the CONFIRMED read by default, with the
developing-bucket path opt-in only and clearly flagged.
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from app.strategy_mtf.timeframe_bias import BULLISH, BEARISH, NEUTRAL, compute_bias  # noqa: E402


def _bar(i, c, start=datetime.datetime(2020, 1, 1, 3, 45), v=1000):
    t = (start + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"t": t, "o": c - 0.1, "h": c + 0.3, "l": c - 0.3, "c": c, "v": v}


def _continuous_bars(n, step=0.0002, start_price=100.0, **kw):
    return [_bar(i, start_price + i * step, **kw) for i in range(n)]


def test_daily_bias_bullish_on_a_clean_uptrend():
    bars = _continuous_bars(6000, step=0.01)   # a few weeks of continuous rise, plenty for daily SMA20
    b = compute_bias(bars, "1d", as_of_ts=bars[-1]["t"])
    assert b.direction == BULLISH
    assert b.score > 0
    assert b.htf_confirmed is True


def test_daily_bias_bearish_on_a_clean_downtrend():
    bars = _continuous_bars(6000, step=-0.01, start_price=500.0)
    b = compute_bias(bars, "1d", as_of_ts=bars[-1]["t"])
    assert b.direction == BEARISH


def test_flat_market_is_neutral():
    bars = _continuous_bars(6000, step=0.0)
    b = compute_bias(bars, "1d", as_of_ts=bars[-1]["t"])
    assert b.direction == NEUTRAL


def test_monthly_bias_needs_real_confirmed_months_not_just_any_data():
    """Too little calendar span for a real 20-month SMA -> insufficient
    history, not a fabricated read."""
    bars = _continuous_bars(3000, step=0.01)   # only a few days of calendar span
    b = compute_bias(bars, "1mo", as_of_ts=bars[-1]["t"])
    assert b.direction == NEUTRAL
    assert b.sma_slow is None
    assert "insufficient" in b.reason


def test_every_result_carries_the_required_audit_fields():
    bars = _continuous_bars(20000, step=0.01)
    for tf in ("1mo", "1w", "1d", "1h", "30m", "15m", "5m"):
        b = compute_bias(bars, tf, as_of_ts=bars[-1]["t"])
        assert b.data_available_ts == bars[-1]["t"]
        assert b.htf_confirmed in (True, False)
        assert hasattr(b, "htf_candle_ts")


def test_developing_bucket_is_never_used_unless_explicitly_opted_in():
    """As of a timestamp that's mid-week, the weekly bucket is still
    developing -- default behaviour must fall back to NEUTRAL/insufficient
    rather than silently reading the still-forming candle."""
    # exactly one bar, mid-week, nowhere near a confirmed weekly close
    bars = [_bar(0, 100.0, start=datetime.datetime(2026, 8, 5, 3, 45))]   # a Wednesday
    b = compute_bias(bars, "1w", as_of_ts=bars[-1]["t"])
    assert b.htf_confirmed is False
    assert b.direction == NEUTRAL

    b_opt_in = compute_bias(bars, "1w", as_of_ts=bars[-1]["t"], allow_developing=True)
    assert b_opt_in.htf_candle_ts is not None   # now it DOES see the developing bucket, explicitly
