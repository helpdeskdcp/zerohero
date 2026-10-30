"""Angel One connector login: one login at a time, retry on an empty body, back off after a failure, no second login path."""
import threading, time

import pytest

from app.connectors import angelone as A


class _Resp:
    def __init__(self, body: bytes, code=200): self.content = body; self.status_code = code
    def json(self):
        import json
        return json.loads(self.content)


OK = b'{"status": true, "data": {"jwtToken": "J1", "feedToken": "F1"}}'


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k, v in (("ANGEL_API_KEY", "k"), ("ANGEL_CLIENT_ID", "c"), ("ANGEL_PASSWORD", "p"), ("ANGEL_TOTP_SECRET", "JBSWY3DPEHPK3PXP")): monkeypatch.setenv(k, v)
    A._session_cache.update({"jwt": None, "feed_token": None, "ts": 0}); A._login_fail.update({"n": 0, "until": 0.0}); monkeypatch.setattr(A, "_RETRY_BACKOFF", 0.0)


def test_many_threads_asking_for_a_session_make_exactly_one_login(monkeypatch):
    calls = []
    def fake(method, url, **kw): calls.append(url); time.sleep(0.05); return _Resp(OK)
    monkeypatch.setattr(A, "_http", fake); out = []
    ts = [threading.Thread(target=lambda: out.append(A._get_jwt())) for _ in range(12)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(calls) == 1 and all(o[0] == "OK" and o[1] == "J1" for o in out)


def test_an_empty_body_is_retried_inside_the_login(monkeypatch):
    seq = [_Resp(b""), _Resp(b"<html>"), _Resp(OK)]
    monkeypatch.setattr(A, "_http", lambda *a, **k: seq.pop(0))
    st, jwt, err = A._login()
    assert (st, jwt) == ("OK", "J1") and not seq


def test_a_failed_login_backs_off_and_nobody_hammers_the_broker(monkeypatch):
    calls = []
    def fake(*a, **k): calls.append(1); return _Resp(b"")
    monkeypatch.setattr(A, "_http", fake)
    st, _, err = A._get_jwt(); n = len(calls)
    assert st == "AUTH_FAILED" and n == A.LOGIN_ATTEMPTS
    for _ in range(5): assert A._get_jwt()[2] == "login_backoff"                     # inside the backoff window: no request at all
    assert len(calls) == n
    A._login_fail["until"] = 0.0; monkeypatch.setattr(A, "_http", lambda *a, **k: _Resp(OK))
    assert A._get_jwt()[0] == "OK" and A._login_fail["n"] == 0                      # after the window it recovers and the counter resets


def test_a_rejected_login_is_auth_failed_with_the_broker_code_and_does_not_retry(monkeypatch):
    calls = []
    def fake(*a, **k): calls.append(1); return _Resp(b'{"status": false, "errorcode": "AB1050", "message": "x"}')
    monkeypatch.setattr(A, "_http", fake)
    assert A._get_jwt() == ("AUTH_FAILED", None, "AB1050") and len(calls) == 1


def test_invalid_token_answer_drops_the_session_and_repeats_the_quote_once(monkeypatch):
    A._session_cache.update({"jwt": "OLD", "feed_token": None, "ts": time.time()}); monkeypatch.setattr(A, "_market_sdk", lambda *a, **k: None)
    answers = [_Resp(b'{"status": false, "errorcode": "AG8001"}'), _Resp(OK), _Resp(b'{"status": true, "data": {"fetched": [{"ltp": 5}]}}')]
    monkeypatch.setattr(A, "_http", lambda *a, **k: answers.pop(0))
    q = A.fetch_market_quote("NSE", "99926000")
    assert q["status"] == "OK" and q["ltp"] == 5 and A._session_cache["jwt"] == "J1"
