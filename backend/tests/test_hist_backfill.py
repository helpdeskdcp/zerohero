"""Backfill runner: windows, checkpoints, resumability, idempotency, error isolation."""
from datetime import date

import pytest

from app.hist_options import backfill as BF
from app.hist_options.expiry_store import ExpiryAudit
from app.hist_options.store import HistOptionStore


def _side(day, n=3):
    from datetime import datetime, timezone
    t0 = int(datetime(day.year, day.month, day.day, 3, 45, tzinfo=timezone.utc).timestamp())
    return {"timestamp": [t0 + 60 * i for i in range(n)], "open": [10.0] * n, "high": [11.0] * n,
            "low": [9.0] * n, "close": [10.5] * n, "volume": [1, 2, 3][:n], "oi": [100, 110, 105][:n],
            "iv": [16.0] * n, "strike": [24000] * n, "spot": [24010.0] * n}


class Fake:
    def __init__(self, fail_on=None, empty_on=None):
        self.calls, self.fail_on, self.empty_on = [], fail_on, empty_on

    def __call__(self, u, ot, f, t, **kw):
        self.calls.append((ot, f, t))
        if self.fail_on == (ot, f):
            raise RuntimeError("boom")
        if self.empty_on == (ot, f):
            return None
        return _side(date.fromisoformat(f))


@pytest.fixture
def store(tmp_path):
    return HistOptionStore(str(tmp_path / "h.db"))


def test_plan_windows_are_contiguous_exclusive_and_bounded():
    w = BF.plan_windows(date(2026, 1, 1), date(2026, 3, 15))
    assert w[0] == (date(2026, 1, 1), date(2026, 1, 31)) and w[-1][1] == date(2026, 3, 15)
    assert all(b > a and (b - a).days <= 30 for a, b in w) and all(w[i][1] == w[i + 1][0] for i in range(len(w) - 1))
    assert BF.plan_windows(date(2026, 1, 1), date(2026, 1, 1)) == []
    with pytest.raises(ValueError):
        BF.plan_windows(date(2026, 1, 1), date(2026, 3, 1), days=31)


def test_run_writes_rows_checkpoints_done_and_resumes_without_refetch(store):
    f = Fake()
    kw = dict(store=store, fetch=f, sleep_sec=0, audit=ExpiryAudit(store.path))
    a = BF.run("NIFTY", date(2026, 8, 3), date(2026, 8, 20), **kw)
    assert a["status"] == "COMPLETE" and a["fetched"] == 2 and a["rows_written"] == 6      # CE+PE x 3 bars
    n_calls = len(f.calls)
    b = BF.run("NIFTY", date(2026, 8, 3), date(2026, 8, 20), **kw)
    assert b["skipped_done"] == 2 and b["fetched"] == 0 and len(f.calls) == n_calls          # resumable, no refetch
    assert len(store.read("NIFTY")) == 6                                                      # idempotent


def test_error_window_is_isolated_checkpointed_and_retried(store):
    f = Fake(fail_on=("PE", "2026-08-03"))
    kw = dict(store=store, sleep_sec=0, audit=ExpiryAudit(store.path))
    a = BF.run("NIFTY", date(2026, 8, 3), date(2026, 8, 20), fetch=f, **kw)
    assert a["status"] == "INCOMPLETE" and a["errors"] == 1 and a["fetched"] == 1
    assert a["checkpoints"]["ERROR"]["windows"] == 1 and a["checkpoints"]["DONE"]["windows"] == 1
    b = BF.run("NIFTY", date(2026, 8, 3), date(2026, 8, 20), fetch=Fake(), **kw)               # retry succeeds
    assert b["fetched"] == 1 and b["skipped_done"] == 1 and b["status"] == "COMPLETE"


def test_empty_window_is_no_data_not_done_and_writes_nothing(store):
    a = BF.run("NIFTY", date(2026, 8, 3), date(2026, 8, 20), store=store, sleep_sec=0,
               fetch=Fake(empty_on=("CE", "2026-08-03")), audit=ExpiryAudit(store.path))
    assert a["no_data"] == 1 and a["status"] == "INCOMPLETE"
    assert [r["option_type"] for r in store.read("NIFTY")] == ["PE"] * 3


def test_max_windows_stops_cleanly_and_unsupported_underlying_is_rejected(store):
    a = BF.run("NIFTY", date(2026, 1, 1), date(2026, 6, 1), store=store, fetch=Fake(), sleep_sec=0,
               max_windows=2, audit=ExpiryAudit(store.path))
    assert a["status"] == "STOPPED_MAX_WINDOWS" and a["fetched"] == 2
    assert BF.run("CRUDEOIL", date(2026, 1, 1), date(2026, 2, 1), store=store, fetch=Fake())["status"] == "UNSUPPORTED"


def test_rows_carry_expiry_provenance_and_rate_label(store):
    BF.run("NIFTY", date(2026, 8, 3), date(2026, 8, 10), store=store, fetch=Fake(), sleep_sec=0,
           audit=ExpiryAudit(store.path))
    r = store.read("NIFTY", "CE")[0]
    gi = r["greek_inputs"]
    assert r["expiry"] == "2026-08-04" and gi["expiry_source"] == "BROKER_METADATA"
    assert gi["rate_source"] == "UNSOURCED_ZERO_DEFAULT" and gi["rate_status"] == "ASSUMED_UNVERIFIED"
    assert r["greek_source"] == "RECONSTRUCTED" and r["iv_source"] == "DIRECT"


def test_pre_metadata_period_stores_direct_data_with_unavailable_greeks(store):
    BF.run("NIFTY", date(2023, 6, 5), date(2023, 6, 12), store=store, fetch=Fake(), sleep_sec=0,
           audit=ExpiryAudit(store.path))
    r = store.read("NIFTY", "CE")[0]
    assert r["expiry"] is None and r["greek_source"] == "UNAVAILABLE" and r["delta"] is None
    assert r["iv"] == pytest.approx(0.16) and r["oi"] == 100              # direct fields still kept


def test_expired_session_aborts_before_any_fetch_or_checkpoint(store):
    f = Fake()
    out = BF.run("NIFTY", date(2026, 8, 3), date(2026, 8, 20), store=store, fetch=f, sleep_sec=0,
                 preflight=lambda: False, audit=ExpiryAudit(store.path))
    assert out["status"] == "AUTH_UNAVAILABLE" and f.calls == []
    assert BF.Checkpoints(store).summary() == {}                       # nothing recorded as 'no data'


def test_session_ok_false_without_credentials_and_on_401(monkeypatch):
    from app.hist_options import rolling
    monkeypatch.setattr(rolling._dc, "_headers", lambda: None)
    assert rolling.session_ok() is False
    monkeypatch.setattr(rolling._dc, "_headers", lambda: {"x": "y"})

    class S:
        def post(self, *a, **k):
            class R: status_code = 401
            return R()
    assert rolling.session_ok(session=S()) is False
