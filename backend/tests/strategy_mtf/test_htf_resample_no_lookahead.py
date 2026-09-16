"""
Explicit no-lookahead / anti-repaint tests for the full HTF resampling
chain: Monthly -> Weekly -> Daily -> 1H -> 30m -> 15m -> 5m, per the user's
own explicit correction. Confirmation must be a pure CALENDAR fact
(as_of_ts >= bucket_end), never a function of what data happens to exist.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from app.strategy_mtf.htf_resample import TIMEFRAMES, resample_confirmed  # noqa: E402


def _bars_5m(start_iso: str, n: int, step_price=0.1):
    """Real, contiguous 5m bars in UTC (matching the Kaggle/real-data
    convention elsewhere in this codebase), starting at `start_iso`."""
    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    out = []
    price = 100.0
    for i in range(n):
        t = (start + timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        price += step_price
        out.append({"t": t, "o": price, "h": price + 0.2, "l": price - 0.2, "c": price, "v": 1000})
    return out


# --------------------------------------------------------------------- #
#  Per-timeframe: confirmed vs developing split is a pure calendar fact  #
# --------------------------------------------------------------------- #
def test_5m_bucket_becomes_confirmed_only_once_its_own_5_minutes_have_elapsed():
    bars = _bars_5m("2026-08-03T03:45:00Z", 3)   # 09:15, 09:20, 09:25 IST -- 3 distinct 5m buckets
    series_mid = resample_confirmed(bars, "5m", as_of_ts=bars[1]["t"])   # as_of = 09:20 IST exactly
    # the 09:20 bucket's own window is [09:20,09:25) -- as_of sitting AT its start is NOT >= its end
    assert series_mid.developing is not None
    assert series_mid.developing.bucket_start.strftime("%H:%M") == "09:20"
    series_later = resample_confirmed(bars, "5m", as_of_ts=bars[2]["t"])   # as_of = 09:25 -- now past 09:20's end
    confirmed_starts = [b.bucket_start.strftime("%H:%M") for b in series_later.confirmed]
    assert "09:20" in confirmed_starts


def test_15m_last_bucket_is_developing_until_its_full_window_closes():
    bars = _bars_5m("2026-08-03T03:45:00Z", 4)   # 09:15..09:30 IST, spans one full 15m bucket + one bar into the next
    as_of_still_forming = bars[3]["t"]           # 09:30 IST -- inside the SECOND 15m bucket [09:30,09:45)
    series = resample_confirmed(bars, "15m", as_of_ts=as_of_still_forming)
    assert series.developing is not None
    assert series.developing.bucket_start.strftime("%H:%M") == "09:30"
    assert any(b.bucket_start.strftime("%H:%M") == "09:15" for b in series.confirmed)


def test_1h_bucket_confirms_only_after_the_full_hour_elapses():
    bars = _bars_5m("2026-08-03T03:45:00Z", 13)   # 09:15 .. 10:15 IST
    mid_hour_ts = bars[6]["t"]     # 09:45 IST -- still inside the 09:00-10:00 IST bucket
    series_mid = resample_confirmed(bars, "1h", as_of_ts=mid_hour_ts)
    assert series_mid.developing is not None and series_mid.developing.bucket_start.strftime("%H:%M") == "09:00"
    assert series_mid.confirmed == []           # nothing has closed yet
    after_hour_ts = bars[-1]["t"]  # 10:15 IST -- the 09:00-10:00 bucket has now closed
    series_after = resample_confirmed(bars, "1h", as_of_ts=after_hour_ts)
    assert any(b.bucket_start.strftime("%H:%M") == "09:00" for b in series_after.confirmed)


def test_30m_bucket_confirmation_is_a_calendar_fact_not_a_data_fact():
    """Sparse data: only ONE real bar exists in a 30m window, then the next
    real bar is from the NEXT day (a large real gap). The bucket must still
    confirm once as_of crosses its calendar end -- confirmation must not
    require a later bar to literally exist."""
    bars = [_bars_5m("2026-08-03T03:45:00Z", 1)[0]]           # a single 09:15 IST bar
    bars += _bars_5m("2026-08-04T03:45:00Z", 1)                # next real bar is the FOLLOWING day
    as_of_next_day = bars[-1]["t"]
    series = resample_confirmed(bars, "30m", as_of_ts=as_of_next_day)
    assert any(b.bucket_start.strftime("%Y-%m-%d %H:%M") == "2026-08-03 09:00" for b in series.confirmed)


def test_daily_bucket_confirms_only_after_midnight_ist_rolls_over():
    bars = _bars_5m("2026-08-03T03:45:00Z", 5)     # all on 2026-08-03 IST
    same_day = resample_confirmed(bars, "1d", as_of_ts=bars[-1]["t"])
    assert same_day.confirmed == [] and same_day.developing is not None
    bars_next_day = bars + _bars_5m("2026-08-04T03:45:00Z", 1)
    next_day = resample_confirmed(bars_next_day, "1d", as_of_ts=bars_next_day[-1]["t"])
    assert any(b.bucket_start.strftime("%Y-%m-%d") == "2026-08-03" for b in next_day.confirmed)


def test_weekly_bucket_confirms_only_after_monday_00_00_ist_of_the_next_week():
    # 2026-08-03 is a Monday -- bars through Friday 2026-08-07 stay in the SAME ISO week
    bars = _bars_5m("2026-08-03T03:45:00Z", 1) + _bars_5m("2026-08-07T03:45:00Z", 1)
    same_week = resample_confirmed(bars, "1w", as_of_ts=bars[-1]["t"])
    assert same_week.confirmed == [] and same_week.developing is not None
    bars_next_week = bars + _bars_5m("2026-08-10T03:45:00Z", 1)   # the following Monday
    next_week = resample_confirmed(bars_next_week, "1w", as_of_ts=bars_next_week[-1]["t"])
    assert len(next_week.confirmed) == 1
    assert next_week.confirmed[0].bucket_start.strftime("%Y-%m-%d") == "2026-08-03"


def test_monthly_bucket_confirms_only_after_the_calendar_month_ends():
    bars = _bars_5m("2026-08-03T03:45:00Z", 1) + _bars_5m("2026-08-28T03:45:00Z", 1)
    same_month = resample_confirmed(bars, "1mo", as_of_ts=bars[-1]["t"])
    assert same_month.confirmed == [] and same_month.developing is not None
    bars_next_month = bars + _bars_5m("2026-09-02T03:45:00Z", 1)
    next_month = resample_confirmed(bars_next_month, "1mo", as_of_ts=bars_next_month[-1]["t"])
    assert len(next_month.confirmed) == 1
    assert next_month.confirmed[0].bucket_start.strftime("%Y-%m") == "2026-08"


# --------------------------------------------------------------------- #
#  Cross-timeframe mutation test (same discipline as every other HTF     #
#  audit in this codebase this session)                                  #
# --------------------------------------------------------------------- #
def test_confirmed_series_is_identical_regardless_of_future_bars_for_every_timeframe():
    prefix = _bars_5m("2026-06-01T03:45:00Z", 2000)   # ~long enough to span months/weeks/days/hours
    T = len(prefix)
    branch_a = prefix + _bars_5m("2026-09-10T03:45:00Z", 50, step_price=0.5)
    branch_b = prefix + _bars_5m("2026-09-10T03:45:00Z", 50, step_price=-50.0)
    as_of = prefix[-1]["t"]
    for tf in TIMEFRAMES:
        out_a = resample_confirmed(branch_a[:T], tf, as_of_ts=as_of)
        out_b = resample_confirmed(branch_b[:T], tf, as_of_ts=as_of)
        assert [b.__dict__ for b in out_a.confirmed] == [b.__dict__ for b in out_b.confirmed], (
            f"LOOK-AHEAD BIAS at tf={tf}: confirmed series changed with only future data differing")
        da = out_a.developing.__dict__ if out_a.developing else None
        db = out_b.developing.__dict__ if out_b.developing else None
        assert da == db, f"LOOK-AHEAD BIAS at tf={tf}: developing bucket changed with only future data differing"


def test_the_mutation_test_is_not_vacuous_full_series_do_differ():
    """As of the branches' own last bar, the diverging day is still the
    DEVELOPING bucket for both (correctly excluded from `confirmed` --
    that's the anti-repaint guarantee working as intended, not a bug) --
    so the counterfactual belongs on `developing`, and separately on
    `confirmed` once as_of rolls far enough forward to actually close it."""
    prefix = _bars_5m("2026-06-01T03:45:00Z", 2000)
    branch_a = prefix + _bars_5m("2026-09-10T03:45:00Z", 50, step_price=0.5)
    branch_b = prefix + _bars_5m("2026-09-10T03:45:00Z", 50, step_price=-50.0)

    out_full_a = resample_confirmed(branch_a, "1d", as_of_ts=branch_a[-1]["t"])
    out_full_b = resample_confirmed(branch_b, "1d", as_of_ts=branch_b[-1]["t"])
    assert out_full_a.developing is not None and out_full_b.developing is not None
    assert out_full_a.developing.c != out_full_b.developing.c

    later_ts = "2026-09-11T10:00:00Z"   # well past midnight IST of the diverging day
    out_later_a = resample_confirmed(branch_a, "1d", as_of_ts=later_ts)
    out_later_b = resample_confirmed(branch_b, "1d", as_of_ts=later_ts)
    assert [b.c for b in out_later_a.confirmed] != [b.c for b in out_later_b.confirmed]
