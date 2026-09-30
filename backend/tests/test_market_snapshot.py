"""app.market_snapshot -- Atomic Market Snapshot (MCP 3.3 section 4). Pure
composition over mocked market_hub/get_chain + a real temp HistStore for
the India VIX read; never touches the live capture DB or a real broker."""
from datetime import datetime, timedelta, timezone

import pytest

from app import market_snapshot as MS
from app.histcap.store import HistStore
from app.optionchain.chain import OptionChain, OptionLeg, StrikeRow

_UTC = timezone.utc


@pytest.fixture
def store(tmp_path):
    return HistStore(str(tmp_path / "mh.db"))


def _seed_vix(store, *, ltp=13.5, age_sec=0.0, net_change=0.1, pct_change=0.7):
    ts = (datetime.now(_UTC) - timedelta(seconds=age_sec)).isoformat()
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO quote_snapshots(received_ts,exch_ts,snap_key,instrument_key,symbol,kind,"
            "exchange,token,session_date_ist,ltp,net_change,pct_change,source) "
            "VALUES(?,?,?,?, 'INDIAVIX','INDEX','NSE','99926017',?,?,?,?,'ANGELONE_QUOTE_FULL')",
            (ts, ts, ts[:19], "NSE:99926017", ts[:10], ltp, net_change, pct_change))


def _chain(rows=1, expiry="29SEP2026"):
    strike_rows = [StrikeRow(strike=23400 + i * 100, ce=OptionLeg(ltp=30, bid=29, ask=30.5, oi=1000))
                   for i in range(rows)]
    return OptionChain(underlying="NIFTY", expiry=expiry, ts="t", source="angelone_captured",
                       spot=23400, rows=strike_rows)


# ---------------------------------------------------------------- _latest_index_quote

def test_latest_index_quote_returns_fresh_row(store):
    _seed_vix(store, ltp=13.5, age_sec=5.0)
    q = MS._latest_index_quote("INDIAVIX", store=store)
    assert q is not None and q["ltp"] == 13.5 and q["age_sec"] < 180


def test_latest_index_quote_none_when_stale(store):
    _seed_vix(store, ltp=13.5, age_sec=99999.0)
    q = MS._latest_index_quote("INDIAVIX", store=store, max_age_sec=180.0)
    assert q is None


def test_latest_index_quote_none_when_no_rows(store):
    assert MS._latest_index_quote("INDIAVIX", store=store) is None


def test_latest_index_quote_picks_the_most_recent_row(store):
    _seed_vix(store, ltp=12.0, age_sec=100.0)
    _seed_vix(store, ltp=13.5, age_sec=5.0)
    q = MS._latest_index_quote("INDIAVIX", store=store, max_age_sec=180.0)
    assert q["ltp"] == 13.5


# ---------------------------------------------------------------- build_snapshot

def test_build_snapshot_clean_case_has_no_conflicts(monkeypatch, store):
    monkeypatch.setattr(MS.market_hub, "snapshot",
                        lambda sym, allow_rest_fallback=True: {"spot": 23400.0,
                                                               "data_quality": {"spot": "ACTUAL (WS feed)"}})
    monkeypatch.setattr(MS, "_get_chain", lambda u, e, allow_network=False: _chain())
    _seed_vix(store, age_sec=5.0)

    snap = MS.build_snapshot("NIFTY", store=store)
    assert snap.conflicts == []
    assert snap.spot == 23400.0 and snap.chain_rows == 1
    assert snap.india_vix is not None and snap.india_vix["ltp"] == 13.5
    import uuid
    uuid.UUID(snap.snapshot_id)   # a real uuid, not a placeholder string


def test_build_snapshot_flags_stale_vix_without_dropping_it(monkeypatch, store):
    monkeypatch.setattr(MS.market_hub, "snapshot",
                        lambda sym, allow_rest_fallback=True: {"spot": 23400.0, "data_quality": {}})
    monkeypatch.setattr(MS, "_get_chain", lambda u, e, allow_network=False: _chain())
    _seed_vix(store, age_sec=170.0)   # inside max_age_sec(180) but past the 60s coherence tolerance

    snap = MS.build_snapshot("NIFTY", store=store, coherence_tolerance_sec=60.0)
    assert snap.india_vix is not None            # never silently dropped
    assert any("INDIAVIX" in c for c in snap.conflicts)


def test_build_snapshot_flags_missing_spot(monkeypatch, store):
    monkeypatch.setattr(MS.market_hub, "snapshot",
                        lambda sym, allow_rest_fallback=True: {"spot": None, "data_quality": {}})
    monkeypatch.setattr(MS, "_get_chain", lambda u, e, allow_network=False: _chain())

    snap = MS.build_snapshot("NIFTY", store=store)
    assert any("no spot" in c for c in snap.conflicts)


def test_build_snapshot_flags_missing_chain(monkeypatch, store):
    monkeypatch.setattr(MS.market_hub, "snapshot",
                        lambda sym, allow_rest_fallback=True: {"spot": 23400.0, "data_quality": {}})
    monkeypatch.setattr(MS, "_get_chain", lambda u, e, allow_network=False: None)

    snap = MS.build_snapshot("NIFTY", store=store)
    assert any("no option chain" in c for c in snap.conflicts)


def test_build_snapshot_never_touches_network_by_default(monkeypatch, store):
    """allow_network defaults False -- get_chain/market_hub must be called
    with allow_network/allow_rest_fallback False unless explicitly opted in."""
    seen = {}
    monkeypatch.setattr(MS.market_hub, "snapshot",
                        lambda sym, allow_rest_fallback=True: seen.setdefault("hub", allow_rest_fallback) or
                        {"spot": 1.0, "data_quality": {}})
    monkeypatch.setattr(MS, "_get_chain",
                        lambda u, e, allow_network=True: seen.setdefault("chain", allow_network) or _chain())
    MS.build_snapshot("NIFTY", store=store)
    assert seen == {"hub": False, "chain": False}
