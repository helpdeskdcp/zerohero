"""app.histcap.cross_broker.compare_captured_quotes -- real, measured
comparison of already-captured ANGELONE_QUOTE_FULL vs DHAN_CHAIN rows.
Real temp HistStore, no network, no live app import."""
import pytest

from app.histcap import cross_broker as CB
from app.histcap.store import HistStore

_COLS = ("received_ts", "exch_ts", "snap_key", "instrument_key", "symbol", "kind",
        "exchange", "token", "expiry", "strike", "option_type", "session_date_ist",
        "ltp", "oi", "volume", "bid", "ask", "source")


@pytest.fixture
def store(tmp_path):
    return HistStore(str(tmp_path / "mh.db"))


def _insert(store, **kw):
    row = {c: None for c in _COLS}
    row.update(kw)
    with store.transaction() as conn:
        conn.execute(
            f"INSERT INTO quote_snapshots({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
            tuple(row[c] for c in _COLS))


def _seed_pair(store, *, strike=22750.0, ot="CE", a_ltp=166.95, d_ltp=167.10,
               a_oi=5670795, d_oi=5670000, a_bid=166.9, d_bid=167.0, a_ts="2026-09-30T10:45:00Z",
               d_ts="2026-09-30T10:45:05Z"):
    a_tok, d_tok = f"A{strike:.0f}{ot}", f"D{strike:.0f}{ot}"
    _insert(store, received_ts=a_ts, exch_ts=a_ts, snap_key=a_ts[:19], instrument_key=f"NFO:{a_tok}",
           symbol="NIFTY", kind="OPTION", exchange="NFO", token=a_tok, expiry="06OCT2026",
           strike=strike, option_type=ot, session_date_ist="2026-09-30",
           ltp=a_ltp, oi=a_oi, volume=1000.0, bid=a_bid, ask=a_bid + 0.5, source="ANGELONE_QUOTE_FULL")
    _insert(store, received_ts=d_ts, exch_ts=d_ts, snap_key=d_ts[:19], instrument_key=f"DHAN:{d_tok}",
           symbol="NIFTY", kind="OPTION", exchange="NFO", token=d_tok, expiry="06OCT2026",
           strike=strike, option_type=ot, session_date_ist="2026-09-30",
           ltp=d_ltp, oi=d_oi, volume=1005.0, bid=d_bid, ask=d_bid + 0.5, source="DHAN_CHAIN")


def test_compare_matches_a_real_pair_and_computes_real_diffs(store):
    _seed_pair(store)
    out = CB.compare_captured_quotes("NIFTY", "06OCT2026", since="2020-01-01T00:00:00Z", store=store)
    assert out["matched"] == 1 and out["angelone_only"] == 0 and out["dhan_only"] == 0
    p = out["pairs"][0]
    assert p["angelone_ltp"] == 166.95 and p["dhan_ltp"] == 167.10
    assert p["ltp_diff_pct"] == pytest.approx((167.10 - 166.95) / 166.95 * 100.0, abs=1e-3)
    assert p["oi_diff"] == pytest.approx(5670000 - 5670795)
    assert p["timestamp_drift_sec"] == pytest.approx(5.0)


def test_compare_reports_angelone_only_strike(store):
    _insert(store, received_ts="2026-09-30T10:45:00Z", exch_ts="2026-09-30T10:45:00Z",
           snap_key="k1", instrument_key="NFO:1", symbol="NIFTY", kind="OPTION", exchange="NFO",
           token="1", expiry="06OCT2026", strike=23000.0, option_type="CE",
           session_date_ist="2026-09-30", ltp=50.0, oi=1000.0, volume=10.0, bid=49.5, ask=50.5,
           source="ANGELONE_QUOTE_FULL")
    out = CB.compare_captured_quotes("NIFTY", "06OCT2026", since="2020-01-01T00:00:00Z", store=store)
    assert out["matched"] == 0 and out["angelone_only"] == 1 and out["dhan_only"] == 0
    assert out["angelone_only_strikes"] == [{"strike": 23000.0, "option_type": "CE"}]


def test_compare_reports_dhan_only_strike(store):
    _insert(store, received_ts="2026-09-30T10:45:00Z", exch_ts="2026-09-30T10:45:00Z",
           snap_key="k1", instrument_key="DHAN:2", symbol="NIFTY", kind="OPTION", exchange="NFO",
           token="2", expiry="06OCT2026", strike=23050.0, option_type="PE",
           session_date_ist="2026-09-30", ltp=60.0, oi=2000.0, volume=20.0, bid=59.5, ask=60.5,
           source="DHAN_CHAIN")
    out = CB.compare_captured_quotes("NIFTY", "06OCT2026", since="2020-01-01T00:00:00Z", store=store)
    assert out["matched"] == 0 and out["dhan_only"] == 1
    assert out["dhan_only_strikes"] == [{"strike": 23050.0, "option_type": "PE"}]


def test_compare_summary_stats_are_real_not_fabricated(store):
    _seed_pair(store, strike=22750.0, a_ltp=100.0, d_ltp=101.0)
    _seed_pair(store, strike=22800.0, a_ltp=50.0, d_ltp=50.0, d_ts="2026-09-30T10:45:00Z")
    out = CB.compare_captured_quotes("NIFTY", "06OCT2026", since="2020-01-01T00:00:00Z", store=store)
    assert out["matched"] == 2
    s = out["summary"]["ltp_diff_pct"]
    assert s["n"] == 2 and s["max_abs"] == pytest.approx(1.0, abs=1e-2)


def test_compare_returns_all_none_stats_when_nothing_captured_yet(store):
    out = CB.compare_captured_quotes("NIFTY", "06OCT2026", since="2020-01-01T00:00:00Z", store=store)
    assert out["matched"] == 0 and out["angelone_only"] == 0 and out["dhan_only"] == 0
    assert out["summary"]["ltp_diff_pct"] == {"avg": None, "max_abs": None, "n": 0}


def test_compare_ignores_other_sources(store):
    _insert(store, received_ts="2026-09-30T10:45:00Z", exch_ts="2026-09-30T10:45:00Z",
           snap_key="k1", instrument_key="X:1", symbol="NIFTY", kind="OPTION", exchange="NFO",
           token="9", expiry="06OCT2026", strike=22900.0, option_type="CE",
           session_date_ist="2026-09-30", ltp=10.0, oi=1.0, volume=1.0, bid=9.5, ask=10.5,
           source="UPSTOX_OPEN")
    out = CB.compare_captured_quotes("NIFTY", "06OCT2026", since="2020-01-01T00:00:00Z", store=store)
    assert out["matched"] == 0 and out["angelone_only"] == 0 and out["dhan_only"] == 0


def test_default_since_excludes_stale_history_not_just_all_time(store):
    # Real incident found live 2026-09-30 while verifying this feature:
    # get_quotes() orders ASC + LIMIT (its own documented footgun -- a
    # long-lived strike/expiry with more history than `limit` silently
    # returns its OLDEST rows). Without a sensible default `since`, this
    # function would have compared ancient AngelOne history against
    # nothing, reporting a false "no overlap" even when both providers
    # currently agree perfectly. A fixed 2026-01-01 row must NOT appear
    # in a call that omits `since` (today's real wall-clock default).
    _seed_pair(store, a_ts="2026-01-01T09:00:00Z", d_ts="2026-01-01T09:00:05Z")
    out = CB.compare_captured_quotes("NIFTY", "06OCT2026", store=store)   # no since= -> real default
    assert out["matched"] == 0 and out["angelone_only"] == 0 and out["dhan_only"] == 0
