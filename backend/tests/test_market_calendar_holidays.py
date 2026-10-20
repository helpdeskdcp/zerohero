import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app import market_calendar as mc

IST = timezone(timedelta(hours=5, minutes=30))
D = date(2026, 10, 20)


@pytest.fixture
def hol(tmp_path, monkeypatch):
    def write(obj):
        p = tmp_path / "h.json"; p.write_text(json.dumps(obj)); monkeypatch.setenv("CHANAKYA_MARKET_HOLIDAYS", str(p))
    return write


def test_list_format_still_closes_every_exchange(hol):
    hol(["2026-10-20"]); assert mc.is_holiday(D) and mc.is_holiday(D, "MCX") and mc.is_holiday(D, "NSE") and not mc.is_holiday(date(2026, 10, 21))


def test_per_exchange_object_format_dussehra_style(hol):
    hol({"NSE": ["2026-10-20"], "BSE": ["2026-10-20"]})
    assert mc.is_holiday(D, "NSE") and mc.is_holiday(D, "NFO") and mc.is_holiday(D, "bse") and mc.is_holiday(D, "BFO")
    assert not mc.is_holiday(D, "MCX") and not mc.is_holiday(D) and not mc.is_holiday(D, "NCO")                          # MCX evening session stays open; global call is not a holiday
    hol({"ALL": ["2026-10-20"], "MCX": ["2026-10-21"]}); assert mc.is_holiday(D, "MCX") and mc.is_holiday(date(2026, 10, 21), "MCX") and not mc.is_holiday(date(2026, 10, 21), "NSE")


def test_segment_status_respects_exchange_holidays(hol):
    hol({"NSE": ["2026-10-20"]}); at = datetime(2026, 10, 20, 11, 0, tzinfo=IST); evening = datetime(2026, 10, 20, 18, 0, tzinfo=IST)
    assert mc.segment_status("NSE", at) == "CLOSED" and mc.segment_status("BSE", at) == "OPEN" and mc.segment_status("MCX", evening) == "OPEN"
    st = mc.status_all(evening); assert st["holidays_by_exchange"]["NSE"] is True and st["holidays_by_exchange"]["MCX"] is False and st["holiday"] is False


def test_missing_or_bad_file_fails_open_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv("CHANAKYA_MARKET_HOLIDAYS", str(tmp_path / "nope.json")); assert not mc.is_holiday(D, "NSE")
    p = tmp_path / "bad.json"; p.write_text("{not json"); monkeypatch.setenv("CHANAKYA_MARKET_HOLIDAYS", str(p)); assert not mc.is_holiday(D, "NSE")
    p.write_text('"a string"'); assert not mc.is_holiday(D, "NSE")


def test_safeguards_use_the_signalled_exchange(hol, monkeypatch):
    from app.autoscalp.safeguards import Safeguards
    today = mc._now_ist().date().isoformat()                                                                           # is_holiday() inside safeguards reads the real clock date
    hol({"NSE": [today]})
    sg = Safeguards({"allow_weekend": False}); monkeypatch.setattr("app.autoscalp.safeguards._mod_now", lambda: (11 * 60, 1))
    monkeypatch.setattr(Safeguards, "_daily_realised", lambda self: 0.0); monkeypatch.setattr(Safeguards, "_trades_today", lambda self: 0)     # (minute-of-day, weekday): 11:00 on a Tuesday
    ok, why = sg.check_entry(open_count=0, feed_connected=True, feed_age_sec=1.0, underlying="NIFTY", side="CE", open_keys=set(), option_premium=100.0, underlying_price=24000.0, exchange="NSE")
    assert not ok and why == "exchange holiday"
    ok2, why2 = sg.check_entry(open_count=0, feed_connected=True, feed_age_sec=1.0, underlying="NATURALGAS", side="CE", open_keys=set(), option_premium=10.0, underlying_price=270.0, exchange="MCX")
    assert why2 != "exchange holiday"                                                                                   # MCX is not an NSE holiday
