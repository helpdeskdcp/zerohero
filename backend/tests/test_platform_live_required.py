"""Provider-health 'required' set: only instruments that are in session AND recently streaming (2026-10-16 regression)."""
from app.platform.live import ACTIVE_WINDOW_SEC, required_instruments
from app.platform.marketdata.health import HEALTHY, IDLE, ProviderFacts, evaluate

ALWAYS = lambda k, now: True


def test_stale_unsubscribed_instruments_do_not_pin_health_degraded():
    now = 1_000_000.0
    # 6 streaming instruments + 54 that ticked hours ago and are no longer subscribed
    last = {f"NSE:{i}": now - 1.0 for i in range(6)} | {f"MCX:{i}": now - 20_000 for i in range(54)}
    req = required_instruments(last, now, ALWAYS)
    assert sorted(req) == sorted(f"NSE:{i}" for i in range(6))
    f = ProviderFacts("ANGELONE", auth_state="VALID", socket_open=True, subscribed=True, last_message_ts=now - 1, instrument_last_ts=last)
    assert evaluate(f, req, now).state == HEALTHY
    # the OLD rule (everything that ever ticked) would have been DEGRADED here
    assert evaluate(f, list(last), now).state != HEALTHY


def test_dead_feed_is_never_healthy():
    now = 1_000_000.0
    last = {f"NSE:{i}": now - (ACTIVE_WINDOW_SEC + 5) for i in range(6)}      # nothing ticked recently
    req = required_instruments(last, now, ALWAYS)
    assert req == []
    f = ProviderFacts("ANGELONE", auth_state="VALID", socket_open=True, subscribed=True, last_message_ts=now - 1, instrument_last_ts=last)
    assert evaluate(f, req, now).state == IDLE


def test_recent_partial_outage_is_still_degraded_within_window():
    now = 1_000_000.0
    last = {"A": now - 1, "B": now - 2, "C": now - 60, "D": now - 70, "E": now - 80}        # 3 of 5 went quiet 1-2 min ago
    req = required_instruments(last, now, ALWAYS)
    assert len(req) == 5
    f = ProviderFacts("ANGELONE", auth_state="VALID", socket_open=True, subscribed=True, last_message_ts=now - 1, instrument_last_ts=last)
    assert evaluate(f, req, now).state != HEALTHY


def test_out_of_session_instruments_excluded():
    now = 1_000_000.0
    last = {"NSE:1": now - 1, "MCX:1": now - 1}
    assert required_instruments(last, now, lambda k, n: k.startswith("MCX")) == ["MCX:1"]
