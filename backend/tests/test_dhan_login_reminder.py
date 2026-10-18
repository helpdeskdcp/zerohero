import datetime as dt
import importlib.util
import os

spec = importlib.util.spec_from_file_location("dlr", os.path.join(os.path.dirname(__file__), "..", "scripts", "dhan_login_reminder.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
IST = M.IST
MON_0820 = dt.datetime(2026, 10, 19, 8, 20, tzinfo=IST)


def test_expired_token_needs_login():
    need, why = M.needs_login({"token_state": "EXPIRED", "expiry_time": "2026-10-19T00:46:55"}, MON_0820)
    assert need and "EXPIRED" in why


def test_token_that_expires_before_the_mcx_close_needs_login():
    need, why = M.needs_login({"token_state": "VALID", "expiry_time": "2026-10-19T10:58:00"}, MON_0820)
    assert need and "before today's MCX close" in why


def test_token_covering_the_whole_session_needs_nothing():
    need, why = M.needs_login({"token_state": "VALID", "expiry_time": "2026-10-20T08:25:00"}, MON_0820)
    assert not need and "valid until" in why


def test_unknown_or_garbled_status_fails_towards_reminding():
    assert M.needs_login({}, MON_0820)[0] and M.needs_login(None, MON_0820)[0]
    assert M.needs_login({"token_state": "VALID", "expiry_time": "not a date"}, MON_0820)[0]
    assert M.needs_login({"token_state": "status unavailable: ConnectionError"}, MON_0820)[0]


def test_message_never_contains_credentials_and_says_no_orders():
    m = M.build_message("token expired", "https://auth.dhan.co/login/consentApp-login?consentAppId=abc")
    assert "consentAppId=abc" in m and "No order is placed" in m and "MPIN) -" in m
    assert "password" not in m.lower() and "token=" not in m.lower()
    assert "could not be created" in M.build_message("x", None)
