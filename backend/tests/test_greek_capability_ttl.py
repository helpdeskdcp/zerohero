"""A 'no greeks' (AB9019) verdict is long-lived only when reached while the segment is OPEN; before the open / after the close it is cached for minutes (2026-10-27 afternoon cascade)."""
from datetime import datetime, timedelta, timezone

import pytest

IST = timezone(timedelta(hours=5, minutes=30))
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from broker.angelone import client as C


def at(h, m, day=27): return datetime(2026, 10, day, h, m, tzinfo=IST)                                      # 27-Oct-2026 is a Tuesday


def test_segment_open_hours():
    assert not C._segment_open("NIFTY", at(9, 0)) and C._segment_open("NIFTY", at(9, 15)) and C._segment_open("NIFTY", at(15, 30)) and not C._segment_open("NIFTY", at(15, 31))
    assert C._segment_open("NATURALGAS", at(9, 0)) and C._segment_open("NATURALGAS", at(23, 30)) and not C._segment_open("NATURALGAS", at(23, 31))
    assert not C._segment_open("SENSEX", at(8, 59)) and C._segment_open("SENSEX", at(12, 0))
    assert not C._segment_open("NIFTY", at(11, 0, day=31))                                                     # Saturday


def _client():
    c = object.__new__(C.AngelOneClient) if hasattr(C, "AngelOneClient") else None
    assert c is not None
    c._greek_capability = {}; c._greek_cap_ttl = 21600.0; c._greek_ttl = 15.0
    return c


def test_unavailable_verdict_ttl_depends_on_the_session(monkeypatch):
    c = _client(); monkeypatch.delenv("ANGEL_GREEK_CLOSED_TTL_SEC", raising=False)
    monkeypatch.setattr(C, "_segment_open", lambda u, now=None: False)
    c._update_greek_capability("NIFTY", {"status": "NO_DATA", "errorcode": "AB9019"})
    v = c._greek_capability["NIFTY"]; assert v["status"] == "UNAVAILABLE" and v["ttl_sec"] == 300.0                      # pre-open verdict: 5 minutes, not 6 hours
    monkeypatch.setenv("ANGEL_GREEK_CLOSED_TTL_SEC", "120"); c._update_greek_capability("BANKNIFTY", {"status": "NO_DATA", "errorcode": "AB9019"}); assert c._greek_capability["BANKNIFTY"]["ttl_sec"] == 120.0
    monkeypatch.setattr(C, "_segment_open", lambda u, now=None: True)
    c._update_greek_capability("SENSEX", {"status": "NO_DATA", "errorcode": "AB9019"}); assert c._greek_capability["SENSEX"]["ttl_sec"] == 21600.0        # a verdict reached while OPEN keeps the long TTL
    c._update_greek_capability("SENSEX", {"status": "OK"}); assert c._greek_capability["SENSEX"]["status"] == "AVAILABLE"
