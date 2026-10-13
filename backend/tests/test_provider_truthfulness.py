"""Provider health must never be inferred from an open socket (Dhan token expiry, Angel additive status)."""
import asyncio
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import dhan_client
from app.connectors import angel_ws, dhan_ws


def _store(fresh_db, expiry_naive: str | None, token="tok"):
    rec = {"accessToken": token, "dhanClientId": "1", "dhanClientName": "N", "expiryTime": expiry_naive, "stored_at": "x"}
    fresh_db.set_setting(dhan_client._SETTINGS_KEY, json.dumps(rec))


def _naive(dt):
    return dt.replace(tzinfo=None).isoformat(timespec="seconds")


def test_token_state_expired_only_when_past_both_timezone_readings(fresh_db):
    now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    _store(fresh_db, "2026-10-01T16:13:30")
    assert dhan_client.token_state(now)["state"] == "EXPIRED"
    # exp 12:30 naive: as UTC = 12:30 (future), as IST = 07:00 UTC (past) -> readings disagree
    assert dhan_client.token_state(now, {"accessToken": "t", "expiryTime": "2026-10-13T12:30:00"})["state"] == "UNCERTAIN"
    assert dhan_client.token_state(now, {"accessToken": "t", "expiryTime": "2026-10-14T12:30:00"})["state"] == "VALID"
    assert dhan_client.token_state(now, {"accessToken": "t", "expiryTime": "garbage"})["state"] == "UNKNOWN"
    assert dhan_client.token_state(now, {"expiryTime": "2026-10-14T12:30:00"})["state"] == "MISSING"


def test_connection_status_keeps_compat_but_flags_expired_token_as_not_authenticated(fresh_db):
    _store(fresh_db, "2026-10-01T16:13:30")
    s = dhan_client.connection_status()
    assert s["connected"] is True and s["token_state"] == "EXPIRED" and s["authenticated"] is False
    assert "accessToken" not in json.dumps(s)
    _store(fresh_db, _naive(datetime.now(timezone.utc) + timedelta(days=2)))
    assert dhan_client.connection_status()["authenticated"] is True


def test_missing_and_corrupt_status_shapes_unchanged(fresh_db):
    assert dhan_client.connection_status() == {"connected": False}


class _FakeWS:
    def __init__(self, frames):
        self.frames, self.sent = list(frames), []

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def send(self, m): self.sent.append(m)

    async def recv(self):
        if self.frames:
            return self.frames.pop(0)
        raise asyncio.CancelledError


class _FakeWebsockets:
    def __init__(self, frames): self.frames = frames
    def connect(self, *a, **k): return _FakeWS(self.frames)


def _feed():
    return dhan_ws.DhanOrderflowWorker(instruments=[{"exchange_segment": "NSE_FNO", "security_id": "1"}])


def test_open_socket_without_a_valid_frame_is_not_connected():
    f = _feed()

    async def go():
        try:
            await f._session(_FakeWebsockets([]), "tok", "cid")
        except asyncio.CancelledError:
            pass
    asyncio.run(go())
    assert f.socket_open is True and f.connected is False                 # opened, but nothing authenticated/flowing yet
    assert f.status()["data_flowing"] is False


def test_loop_does_not_reconnect_with_an_expired_token(fresh_db, monkeypatch):
    _store(fresh_db, "2026-10-01T16:13:30")
    monkeypatch.setenv("DHAN_CLIENT_ID", "1")
    f = _feed()
    attempts = []
    sleeps = []

    async def fake_sleep(secs):
        sleeps.append(secs)
        f._stop.set()
    monkeypatch.setattr(f, "_sleep", fake_sleep)

    class W:
        @staticmethod
        def connect(*a, **k):
            attempts.append(1)
            raise AssertionError("must not connect with an expired token")
    monkeypatch.setitem(__import__("sys").modules, "websockets", W)

    async def noop(): return None
    monkeypatch.setattr(f, "_refresh_instruments", noop)
    asyncio.run(f._loop())
    assert attempts == [] and sleeps == [300]
    assert f.auth_state == "EXPIRED" and "token expired" in f.last_error and f.connected is False


def test_angel_status_additive_fields_report_real_data_flow():
    feed = angel_ws.AngelMarketFeed()
    feed.connected = True
    feed._desired = {"99926000": {}}
    feed._active = {"99926000"}
    now = time.time()
    feed.last_msg_ts = now
    feed.ltp = {"99926000": {"ltp": 1.0, "ts_ms": 0, "recv": now}, "OLD": {"ltp": 1.0, "ts_ms": 0, "recv": now - 100}}
    st = feed.status()
    assert st["connected"] is True and st["socket_open"] is True and st["data_flowing"] is True and st["subscribed"] is True
    assert st["fresh_tokens"] == ["99926000"] and st["stale_tokens"] == ["OLD"]
    feed.last_msg_ts = now - 60
    assert feed.status()["data_flowing"] is False                          # open socket but silent => not flowing
