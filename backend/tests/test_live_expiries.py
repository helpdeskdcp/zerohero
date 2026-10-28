"""The default (first) expiry must be the current tradable one: expiries before today's IST date are never offered."""
import time

from app.arena import contracts as CT

CAP = {"day": "2026-10-27", "expiries": {"2026-10-27": {"strikes": [25000.0], "raw": "27OCT2026"}, "2026-11-03": {"strikes": [25000.0], "raw": "03NOV2026"}, "2026-11-10": {"strikes": [25000.0], "raw": "10NOV2026"}}}


def test_yesterdays_expiry_is_dropped_and_the_next_one_becomes_the_default():
    out = CT.live_expiries(CAP, today="2026-10-28"); assert list(out["expiries"]) == ["2026-11-03", "2026-11-10"] and out["day"] == "2026-10-27"
    assert CAP["expiries"].keys() >= {"2026-10-27"}                                           # the input is not mutated


def test_an_expiry_dated_today_is_still_tradable_and_listed_first():
    assert list(CT.live_expiries(CAP, today="2026-10-27")["expiries"]) == ["2026-10-27", "2026-11-03", "2026-11-10"]
    assert list(CT.live_expiries(CAP, today="2026-11-03")["expiries"]) == ["2026-11-03", "2026-11-10"]


def test_nothing_left_gives_an_empty_list_not_an_old_expiry():
    assert CT.live_expiries(CAP, today="2026-12-01")["expiries"] == {}


def test_captured_applies_it_to_cached_and_file_loaded_answers_but_not_to_a_historical_day(monkeypatch):
    stale = {"day": "2020-01-02", "expiries": {"2020-01-02": {"strikes": [1.0], "raw": "02JAN2020"}, "2999-01-01": {"strikes": [1.0], "raw": "01JAN2999"}}}
    monkeypatch.setitem(CT._cache, ("ZZ", None, None), (time.time(), stale)); monkeypatch.setitem(CT._cache, ("ZZ", "2020-01-02", None), (time.time(), stale)); CT._file_loaded[0] = True
    assert list(CT.captured("ZZ")["expiries"]) == ["2999-01-01"]                               # live view: only tradable expiries
    assert list(CT.captured("ZZ", "2020-01-02")["expiries"]) == ["2020-01-02", "2999-01-01"]    # an explicit past session day is a historical query: untouched
    monkeypatch.setitem(CT._cache, ("YY", None, None), (time.time(), stale)); assert list(CT.captured_fresh("YY")["expiries"]) == ["2999-01-01"]
