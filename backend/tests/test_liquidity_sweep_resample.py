"""
app/liquidity_sweep/resample.py -- real-data UP-aggregation (5m -> coarser),
never fine-graining. Verifies exact OHLCV aggregation, IST alignment, and
that resampling never invents a bar from data it wasn't given (no future
leakage: the output never contains more bars' worth of time than the input
covers, and the last bucket is allowed to be partial).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.liquidity_sweep.resample import resample_bars, resample_daily  # noqa: E402


def _bar(t, o, h, l, c, v=1000):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def test_resample_bars_aggregates_ohlcv_exactly():
    # 03:45, 03:50, 03:55 UTC = 09:15, 09:20, 09:25 IST -- one real 15m bucket
    bars = [
        _bar("2026-01-05T03:45:00Z", 100, 101, 99, 100.5, 1000),
        _bar("2026-01-05T03:50:00Z", 100.5, 102, 100, 101.5, 1200),
        _bar("2026-01-05T03:55:00Z", 101.5, 101.8, 99.5, 100.0, 800),
    ]
    out = resample_bars(bars, 15)
    assert len(out) == 1
    bucket = out[0]
    assert bucket["o"] == 100          # first bar's open
    assert bucket["c"] == 100.0        # last bar's close
    assert bucket["h"] == 102          # max high across all 3
    assert bucket["l"] == 99           # min low across all 3
    assert bucket["v"] == 3000         # summed volume
    assert bucket["t"] == "2026-01-05T03:55:00Z"   # timestamped at the last real bar


def test_resample_bars_splits_across_bucket_boundary():
    bars = [
        _bar("2026-01-05T03:45:00Z", 100, 101, 99, 100),   # 09:15 IST -> 09:15 bucket
        _bar("2026-01-05T03:50:00Z", 100, 101, 99, 100),   # 09:20 IST -> same bucket
        _bar("2026-01-05T04:00:00Z", 100, 101, 99, 100),   # 09:30 IST -> next 15m bucket
    ]
    out = resample_bars(bars, 15)
    assert len(out) == 2


def test_resample_bars_last_bucket_may_be_partial_not_future_filled():
    """Only 2 of a 3-bar 15m bucket are supplied (simulating a live mid-
    candle view) -- output must reflect only the 2 given bars, never a 3rd
    invented one."""
    bars = [
        _bar("2026-01-05T03:45:00Z", 100, 101, 99, 100),
        _bar("2026-01-05T03:50:00Z", 100, 103, 99, 102),
    ]
    out = resample_bars(bars, 15)
    assert len(out) == 1
    assert out[0]["h"] == 103
    assert out[0]["c"] == 102


def test_resample_bars_empty_input_returns_empty():
    assert resample_bars([], 15) == []


def test_resample_daily_groups_by_ist_session_date():
    bars = [
        _bar("2026-01-05T03:45:00Z", 100, 105, 95, 102),   # day 1
        _bar("2026-01-05T09:55:00Z", 102, 106, 101, 103),  # still day 1 (15:25 IST)
        _bar("2026-01-06T03:45:00Z", 103, 104, 100, 101),  # day 2
    ]
    out = resample_daily(bars)
    assert len(out) == 2
    assert out[0]["h"] == 106 and out[0]["l"] == 95
    assert out[0]["o"] == 100 and out[0]["c"] == 103
    assert out[1]["o"] == 103 and out[1]["c"] == 101


def test_resample_never_produces_more_time_span_than_the_input():
    """A cheap sanity guard against any future-leakage bug: the number of
    resampled buckets can never exceed the number of input bars."""
    bars = [_bar(f"2026-01-05T{3+i//12:02d}:{(45+5*i) % 60:02d}:00Z", 100, 101, 99, 100)
            for i in range(20)]
    out = resample_bars(bars, 15)
    assert len(out) <= len(bars)
