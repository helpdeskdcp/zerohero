"""NSE / MCX session windows + restart guard."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import market_calendar as mc

_IST = timezone(timedelta(hours=5, minutes=30))


def _t(h, m, *, wd=0):
    # 2026-08-31 is a Monday
    return datetime(2026, 8, 31, h, m, tzinfo=_IST) + timedelta(days=wd)


def test_nse_segments():
    assert mc.segment_status("NSE", _t(8, 30)) == "CLOSED"
    assert mc.segment_status("NSE", _t(9, 5)) == "PRE_OPEN"
    assert mc.segment_status("NSE", _t(9, 20)) == "OPEN"
    assert mc.segment_status("NSE", _t(15, 25)) == "OPEN"
    assert mc.segment_status("NSE", _t(15, 35)) == "CLOSED"     # regular close 15:30
    assert mc.segment_status("NSE", _t(15, 55)) == "POST_CLOSE"
    assert mc.segment_status("NSE", _t(9, 20, wd=6)) == "CLOSED"  # Sunday


def test_mcx_runs_late():
    assert mc.segment_status("MCX", _t(9, 20)) == "OPEN"
    assert mc.segment_status("MCX", _t(15, 45)) == "OPEN"        # NSE shut, MCX still open
    assert mc.segment_status("MCX", _t(22, 0)) == "OPEN"
    assert mc.segment_status("MCX", _t(23, 40)) == "POST_CLOSE"
    assert mc.is_trading("MCX", _t(18, 0)) is True
    assert mc.is_trading("NSE", _t(18, 0)) is False


def test_market_open_flag_tristate():
    assert mc.market_open_flag("NSE", _t(11, 0)) is True
    assert mc.market_open_flag("NSE", _t(20, 0)) is False
    assert mc.market_open_flag("FOREX", _t(11, 0)) is None       # no window table


def test_closed_regime():
    assert mc.closed_regime("NSE", _t(11, 0)) is None
    assert mc.closed_regime("NSE", _t(16, 30)) == "MARKET_CLOSED"


def test_restart_guard():
    ok, why = mc.restart_allowed(_t(11, 0))
    assert ok is False and "OPEN" in why                        # markets live
    ok, why = mc.restart_allowed(_t(15, 45))
    assert ok is False and "MCX" in why                         # MCX still open
    ok, why = mc.restart_allowed(_t(23, 45))
    assert ok is True                                           # after MCX close
    ok, why = mc.restart_allowed(_t(3, 0))
    assert ok is True                                           # overnight
    ok, why = mc.restart_allowed(_t(11, 0, wd=6))
    assert ok is True and "non-trading" in why                  # weekend


def test_windows_override_env(monkeypatch):
    monkeypatch.setenv("CHANAKYA_MARKET_WINDOWS",
                       '{"MCX": {"open": [540, 1435]}}')          # evening to 23:55
    assert mc.segment_status("MCX", _t(23, 50)) == "OPEN"
