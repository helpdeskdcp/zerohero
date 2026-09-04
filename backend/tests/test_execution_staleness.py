"""
app/execution/staleness.py — delay-aware guardrails that block *new* entries
when the market feed or reconciliation loop has gone stale. Pure logic, no
DB / no network / no clock sleeps (an explicit `now` is passed in).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.execution.staleness import Clocks, assess, age_sec   # noqa: E402

NOW = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)


def _iso(seconds_ago: float) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).isoformat()


def test_fresh_feed_allows_new_entries():
    c = Clocks(market_data_ts=_iso(2), last_reconcile_ts=_iso(5))
    r = assess(c, now=NOW)
    assert r.allow_new_entries is True
    assert r.feed_stale is False
    assert r.reconcile_stale is False
    assert r.reasons == []


def test_stale_feed_blocks_new_entries_but_reports_why():
    c = Clocks(market_data_ts=_iso(45), last_reconcile_ts=_iso(5))
    r = assess(c, max_ltp_age=20.0, now=NOW)
    assert r.feed_stale is True
    assert r.allow_new_entries is False
    assert any("market data" in x for x in r.reasons)


def test_missing_market_data_ts_blocks_new_entries():
    c = Clocks(market_data_ts=None, last_reconcile_ts=_iso(5))
    r = assess(c, now=NOW)
    assert r.allow_new_entries is False
    assert any("no market-data timestamp" in x for x in r.reasons)


def test_stale_reconcile_freezes_entries_even_with_fresh_feed():
    c = Clocks(market_data_ts=_iso(2), last_reconcile_ts=_iso(200))
    r = assess(c, max_reconcile_age=90.0, now=NOW)
    assert r.reconcile_stale is True
    assert r.allow_new_entries is False


def test_missing_reconcile_ts_does_not_by_itself_block():
    """No reconcile timestamp yet (e.g. brand new session, nothing to
    reconcile) must not be treated the same as a STALE reconcile."""
    c = Clocks(market_data_ts=_iso(2), last_reconcile_ts=None)
    r = assess(c, now=NOW)
    assert r.reconcile_stale is False
    assert r.allow_new_entries is True


def test_age_sec_handles_epoch_seconds_and_millis():
    epoch_s = NOW.timestamp() - 10
    epoch_ms = (NOW.timestamp() - 10) * 1000
    assert abs(age_sec(epoch_s, now=NOW) - 10) < 1
    assert abs(age_sec(epoch_ms, now=NOW) - 10) < 1


def test_age_sec_handles_z_suffix_iso():
    ts = (NOW - timedelta(seconds=7)).isoformat().replace("+00:00", "Z")
    assert abs(age_sec(ts, now=NOW) - 7) < 1


def test_age_sec_unparseable_returns_none():
    assert age_sec("not-a-timestamp", now=NOW) is None
    assert age_sec(None, now=NOW) is None


def test_age_sec_never_negative_for_future_ts():
    future = (NOW + timedelta(seconds=30)).isoformat()
    assert age_sec(future, now=NOW) == 0.0
