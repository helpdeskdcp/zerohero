"""Regression (found by the Pine-parity audit, 2026-10-14): the weekday formula ((days)+4)%7 was one day off, so every Friday was
treated as a weekend and every Sunday as a weekday in the platform candle engine, hybrid_mp, smart_bos and the hybrid dashboard.
These tests use REAL calendar dates for all seven weekdays (earlier tests only used a Saturday, which was right by accident)."""
import datetime as dt

import numpy as np

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
WEEK = [(2026, 10, d) for d in range(12, 19)]          # Mon 12 .. Sun 18 Oct 2026
EXPECT_OPEN = [True, True, True, True, True, False, False]


def at(y, m, d, hh=10, mm=0):
    return dt.datetime(y, m, d, hh, mm, tzinfo=IST)


def test_platform_candle_session_fn_every_weekday():
    from app.platform.candles.engine_1s import make_session_fn
    f = make_session_fn()
    for (y, m, d), exp in zip(WEEK, EXPECT_OPEN):
        assert f("NSE:1", at(y, m, d).timestamp()) is exp, (y, m, d)
        assert f("MCX:1", at(y, m, d).timestamp()) is exp, (y, m, d)       # MCX 09:00-23:30 is also Mon-Fri


def test_hybrid_engine_weekday_helper_matches_calendar():
    from app.research_engines.hybrid_mp.engine import weekday_ist
    ts = np.array([at(y, m, d).timestamp() for (y, m, d) in WEEK])
    assert weekday_ist(ts).tolist() == [dt.date(y, m, d).weekday() for (y, m, d) in WEEK]


def test_hybrid_engine_trades_on_fridays():
    """A signal on a Friday session must be generated (the old formula silently dropped the whole day)."""
    from app.research_engines.hybrid_mp.engine import Params, run
    rows = [(100, 101, 99, 100), (100, 102, 99.5, 101), (101, 105, 100, 102), (102, 103, 100.5, 101.5), (101.5, 102.5, 100, 101),
            (101, 101.5, 100, 100.5), (100.5, 106, 100.4, 105.8), (105.8, 106, 105, 105.5)]
    a = np.array(rows, float)
    for (y, m, d), exp in zip(WEEK, EXPECT_OPEN):
        t0 = int(at(y, m, d, 9, 15).timestamp())
        b = {"t": (t0 + np.arange(len(rows)) * 60).astype(np.int64), "o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3], "v": np.zeros(len(rows))}
        _, st = run(b, Params(pivot_len=2, atr_len=3, continuation_lookback=3))
        assert (st["signals_buy"] == 1) is exp, (y, m, d)


def test_smart_bos_engine_weekday_mask():
    import inspect
    from app.research_engines.smart_bos import engine as sb
    src = inspect.getsource(sb.generate_signals)
    assert "// 86400 + 3) % 7" in src and "// 86400 + 4) % 7" not in src
    ts = np.array([at(y, m, d).timestamp() for (y, m, d) in WEEK], dtype=np.int64)
    wd = ((ts + 19800) // 86400 + 3) % 7
    assert (wd < 5).tolist() == EXPECT_OPEN


def test_hybrid_dashboard_market_open_by_wall_clock():
    from app.hybrid_mp_api import _nse_open
    for (y, m, d), exp in zip(WEEK, EXPECT_OPEN):
        assert _nse_open(at(y, m, d).timestamp() + 19800) is exp, (y, m, d)
    assert not _nse_open(at(2026, 10, 14, 9, 14).timestamp() + 19800) and _nse_open(at(2026, 10, 14, 9, 15).timestamp() + 19800)
    assert _nse_open(at(2026, 10, 14, 15, 29).timestamp() + 19800) and not _nse_open(at(2026, 10, 14, 15, 30).timestamp() + 19800)
