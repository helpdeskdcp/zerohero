"""Historical reads select one provider/contract, and never borrow future data."""
from calendar_fixtures import synthetic_calendars

import sqlite3
from datetime import datetime, timezone

import pytest

from app import market_hub as hub
from app.histcap.store import HistStore

DAY = "2026-09-02"
NOW = "2026-09-02T04:30:00Z"
EXP = "08SEP2026"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "history.db")
    HistStore(path)
    monkeypatch.setattr(hub, "_HDB", path)
    return path


def candle(db, source, *, ts="2026-09-02T04:20:00Z", kind="FUTURE", expiry=EXP,
           value=100, token=None):
    token = token or source + str(expiry) + kind
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO market_candles(received_ts,instrument_key,symbol,kind,exchange,token,"
                  "expiry,tf,bar_start,session_date_ist,o,h,l,c,v,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (NOW, "NFO:" + token, "NIFTY", kind, "NFO" if kind == "FUTURE" else "NSE",
                   token, expiry if kind == "FUTURE" else None, "5m", ts, DAY,
                   value, value + 1, value - 1, value, 10, source))


def quote(db, source, *, ts=NOW, expiry=EXP, side="CE", value=100, oi=1000, exch_ts=None):
    token = expiry + side
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO quote_snapshots(received_ts,exch_ts,snap_key,instrument_key,symbol,kind,"
                  "exchange,token,expiry,strike,option_type,session_date_ist,ltp,oi,source) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (ts, exch_ts, ts, "NFO:" + token, "NIFTY", "OPTION", "NFO", token,
                   expiry, 24000, side, DAY, value, oi, source))


def test_default_selects_dhan_before_angelone_without_merging(db):
    candle(db, "ANGELONE_CANDLES", value=200)
    candle(db, "DHAN_HISTORICAL", value=100, kind="INDEX")
    rows = hub.session_bars("NIFTY", DAY, as_of=NOW)
    assert len(rows) == 1 and rows[0]["c"] == 100
    assert rows[0]["source"] == "DHAN_HISTORICAL"
    assert rows[0]["provider"] == "DHAN" and not rows[0]["fallback_used"]


@pytest.mark.parametrize("provider,source", [("DHAN", "DHAN_HISTORICAL"),
                                            ("ANGELONE", "ANGELONE_CANDLES")])
def test_explicit_provider_never_falls_through(db, provider, source):
    other = "ANGELONE_CANDLES" if provider == "DHAN" else "DHAN_HISTORICAL"
    candle(db, other)
    result = hub.session_bars_result("NIFTY", DAY, provider=provider, as_of=NOW)
    assert result["status"] == "UNAVAILABLE" and result["rows"] == []
    candle(db, source)
    rows = hub.session_bars("NIFTY", DAY, provider=provider, as_of=NOW)
    assert {r["source"] for r in rows} == {source}


@pytest.mark.parametrize("bad", [float("inf"), -10])
def test_invalid_primary_falls_back_as_a_whole_with_reason(db, bad):
    candle(db, "DHAN_HISTORICAL", value=bad)
    candle(db, "ANGELONE_CANDLES", value=200)
    result = hub.session_bars_result("NIFTY", DAY, as_of=NOW)
    assert result["provider"] == "ANGELONE" and result["fallback_used"]
    assert result["fallback_reason"] == "INVALID_DATA"
    assert result["rows"][0]["c"] == 200


def test_interior_gap_does_not_borrow_another_providers_bar(db):
    for ts in ("04:10", "04:20"):
        candle(db, "DHAN_HISTORICAL", ts=f"{DAY}T{ts}:00Z")
    for ts in ("04:10", "04:15", "04:20"):
        candle(db, "ANGELONE_CANDLES", ts=f"{DAY}T{ts}:00Z")
    result = hub.session_bars_result("NIFTY", DAY, as_of=NOW)
    assert result["provider"] == "ANGELONE" and result["fallback_reason"] == "INCOMPLETE_SERIES"
    assert {r["provider"] for r in result["rows"]} == {"ANGELONE"}


def test_explicit_expiry_and_closed_bar_cutoff(db):
    candle(db, "DHAN_HISTORICAL", expiry=EXP, value=100)
    candle(db, "DHAN_HISTORICAL", expiry="15SEP2026", value=200)
    candle(db, "DHAN_HISTORICAL", expiry=EXP, ts="2026-09-02T04:30:00Z", value=300)
    rows = hub.session_bars("NIFTY", DAY, expiry=EXP, as_of="2026-09-02T10:00:00+05:30")
    assert len(rows) == 1 and rows[0]["c"] == 100 and rows[0]["expiry"] == EXP
    assert rows[0]["series_type"] == "CONTRACT"


def test_legacy_session_rollover_dedup_is_explicit_and_provenance_survives(db):
    candle(db, "DHAN_HISTORICAL", expiry=EXP, value=100)
    candle(db, "DHAN_HISTORICAL", expiry="15SEP2026", value=200)
    rows = hub.session_bars("NIFTY", DAY, as_of=NOW)
    assert len(rows) == 1 and rows[0]["c"] == 100
    assert rows[0]["series_type"] == "UNDERLYING_SESSION" and rows[0]["expiry"] == EXP


def test_option_exact_expiry_and_provider_isolate_quotes_and_oi(db):
    quote(db, "DHAN_CHAIN", value=100, oi=1000)
    quote(db, "DHAN_CHAIN", expiry="15SEP2026", value=300, oi=3000)
    quote(db, "ANGELONE_QUOTE_FULL", value=200, oi=2000)
    r = hub._hist_chain("NIFTY", DAY, expiry=EXP, as_of=NOW)
    assert r["provider"] == "DHAN" and r["expiry"] == EXP
    assert r["chain"][0]["ce_ltp"] == 100 and r["chain"][0]["ce_oi"] == 1000
    assert r["chain"][0]["ce_oi_source"] == "DHAN_CHAIN"
    r = hub._hist_chain("NIFTY", DAY, provider="ANGELONE", expiry=EXP, as_of=NOW)
    assert r["chain"][0]["ce_ltp"] == 200


def test_explicit_angelone_does_not_return_dhan_only(db):
    quote(db, "DHAN_CHAIN")
    assert hub._hist_chain("NIFTY", DAY, provider="ANGELONE", expiry=EXP, as_of=NOW) is None


@pytest.mark.parametrize("ts", ["2026-09-02T04:29:00Z", NOW, "2026-09-02T04:31:00Z"])
def test_option_asof_is_backward_only(db, ts):
    quote(db, "DHAN_CHAIN", ts=ts)
    r = hub._hist_chain("NIFTY", DAY, expiry=EXP, as_of="2026-09-02T10:00:00+05:30")
    if ts > NOW:
        assert r is None
    else:
        assert r and r["age_sec"] >= 0


def test_exchange_future_timestamp_is_also_excluded(db):
    quote(db, "DHAN_CHAIN", exch_ts="2026-09-02T04:31:00Z")
    assert hub._hist_chain("NIFTY", DAY, expiry=EXP, as_of=NOW) is None


def test_expiry_omission_requires_provider_calendar_evidence(db):
    quote(db, "DHAN_CHAIN")
    quote(db, "DHAN_CHAIN", expiry="15SEP2026")
    assert hub.session_option_quotes("NIFTY", DAY, as_of=NOW) == {}
    assert hub._hist_chain("NIFTY", DAY, as_of=NOW) is None


@pytest.mark.parametrize("alias,expected", [("AUTO", EXP), ("NEXT", "15SEP2026")])
def test_offline_aliases_use_selected_provider_expiry_evidence(db, alias, expected):
    quote(db, "DHAN_CHAIN")
    quote(db, "DHAN_CHAIN", expiry="15SEP2026")
    result = hub.session_option_quotes_result("NIFTY", DAY, expiry=alias, as_of=NOW,
        expiry_dates=synthetic_calendars({"DHAN": ["15SEP2026", EXP], "ANGELONE": ["30SEP2026"]}))
    assert result["expiry"] == expected and result["provider"] == "DHAN"
    assert {r["expiry"] for r in result["rows"]} == {expected}


def test_premium_records_retain_provider_expiry_and_time(db):
    quote(db, "DHAN_CHAIN")
    quote(db, "ANGELONE_QUOTE_FULL", expiry="15SEP2026", value=900)
    series = hub.session_option_quotes("NIFTY", DAY, expiry=EXP, as_of=NOW)
    row = series[(24000, "CE")][0]
    assert row["provider"] == "DHAN" and row["expiry"] == EXP and row["ltp"] == 100
    assert row["timestamp"] == NOW and row["source"] == "DHAN_CHAIN"


def test_public_chain_historical_request_never_calls_live_provider(db, monkeypatch):
    from app.optionchain.sources import dhan_chain
    def forbidden(*args, **kwargs):
        raise AssertionError("historical queries must never contact a live provider")
    monkeypatch.setattr(dhan_chain, "fetch", forbidden)
    monkeypatch.setattr(hub, "_throttle", forbidden)
    quote(db, "DHAN_CHAIN")
    r = hub.get_chain("NIFTY", expiry_mode=EXP, as_of=NOW)
    assert r["provider"] == "DHAN" and r["chain"][0]["ce_ltp"] == 100
    r = hub.get_chain("NIFTY", provider="ANGELONE", expiry_mode=EXP, as_of=NOW)
    assert r["chain"] == [] and r["status"] == "UNAVAILABLE"


def test_public_chain_fallback_is_visible_and_explicit_dhan_is_strict(db):
    quote(db, "ANGELONE_QUOTE_FULL")
    r = hub.get_chain("NIFTY", expiry_mode=EXP, as_of=NOW, allow_rest_fallback=False)
    assert r["provider"] == "ANGELONE" and r["fallback_used"]
    assert r["requested_provider"] is None
    assert r["fallback_reason"] == "NO_VALID_CAPTURE_OR_PROVIDER_RESPONSE"
    assert r["chain"][0]["fallback_reason"] == r["fallback_reason"]
    r = hub.get_chain("NIFTY", provider="DHAN", expiry_mode=EXP, as_of=NOW, allow_rest_fallback=False)
    assert r["chain"] == [] and r["status"] == "UNAVAILABLE"


def test_optionchain_resolver_historical_default_is_dhan_and_explicit_angelone_is_strict(db, monkeypatch):
    from app.optionchain import resolve
    from app.optionchain.sources import dhan_chain
    quote(db, "DHAN_CHAIN")
    def forbidden(*a, **kw):
        raise AssertionError("as-of resolution cannot fetch today's broker data")
    monkeypatch.setattr(dhan_chain, "fetch", forbidden)
    chain = resolve.get_chain("NIFTY", EXP, db_path=db, at_ts=NOW, allow_network=True)
    assert chain is not None and chain.source == "dhan_captured"
    assert chain.leg(24000, "CE").provenance["quote_source"] == "DHAN_CHAIN"
    assert resolve.get_chain("NIFTY", EXP, db_path=db, at_ts=NOW,
                             provider="ANGELONE", allow_network=False) is None


def test_spot_reader_is_provider_specific_and_never_returns_future(db, monkeypatch):
    from app.mathematical_confluence import context
    from app.histcap import store
    monkeypatch.setattr(store, "DB_PATH", db)
    quote(db, "ANGELONE_QUOTE_FULL", value=200)
    quote(db, "DHAN_CHAIN", value=100, ts="2026-09-02T04:31:00Z")
    with sqlite3.connect(db) as c:
        c.execute("UPDATE quote_snapshots SET kind='INDEX', expiry=NULL")
    assert context._spot_from_histcap("NIFTY", provider="DHAN", as_of=NOW) is None
    r = context._spot_from_histcap("NIFTY", as_of=NOW)
    assert r["ltp"] == 200 and r["provider"] == "ANGELONE" and r["fallback_used"]


def test_fallback_resolves_its_own_expiry_and_never_uses_primary_calendar(db):
    quote(db, "DHAN_CHAIN", expiry=EXP)
    quote(db, "ANGELONE_QUOTE_FULL", expiry=EXP, value=200)
    result = hub.session_option_quotes_result("NIFTY", DAY, expiry="AUTO", as_of=NOW,
        expiry_dates=synthetic_calendars({"DHAN": ["15SEP2026"], "ANGELONE": [EXP]}))
    assert result["provider"] == "ANGELONE" and result["expiry"] == EXP
    assert result["fallback_used"] and result["rows"][0]["ltp"] == 200


def test_calendar_version_provenance_survives_all_readers(db):
    """Calendar attribution survives raw quote -> hub -> canonical chain -> leg."""
    from app.optionchain.resolve import get_chain
    evidence = synthetic_calendars({"DHAN": [EXP]})
    quote(db, "DHAN_CHAIN")
    result = hub.session_option_quotes_result("NIFTY", DAY, expiry="AUTO", as_of=NOW, expiry_dates=evidence)
    assert result["calendar"] == result["rows"][0]["calendar"]
    assert result["calendar"]["calendar_version"] == "synthetic-v1"
    chain = get_chain("NIFTY", "AUTO", db_path=db, at_ts=NOW, expiry_dates=evidence)
    assert chain.capability["calendar"] == chain.leg(24000, "CE").provenance["calendar"]
    assert chain.capability["calendar"]["provider"] == "DHAN"
    h = hub.get_chain("NIFTY", as_of=NOW, expiry_dates=evidence)
    assert h["calendar"] == h["chain"][0]["calendar"]


def test_missing_primary_calendar_can_fall_back_only_to_verified_angelone(db):
    """Fallback resolves AngelOne evidence; explicit Dhan cannot use that evidence."""
    quote(db, "DHAN_CHAIN")
    quote(db, "ANGELONE_QUOTE_FULL", value=200)
    evidence = synthetic_calendars({"ANGELONE": [EXP]})
    result = hub.session_option_quotes_result("NIFTY", DAY, as_of=NOW, expiry_dates=evidence)
    assert result["provider"] == "ANGELONE" and result["fallback_used"]
    assert result["fallback_reason"] == "CALENDAR_EVIDENCE_UNAVAILABLE"
    assert result["calendar"]["provider"] == "ANGELONE"
    result = hub.session_option_quotes_result("NIFTY", DAY, provider="DHAN", as_of=NOW, expiry_dates=evidence)
    assert result["status"] == "UNAVAILABLE" and not result["rows"]
    assert result["reason"] == "CALENDAR_EVIDENCE_UNAVAILABLE"


def test_bad_primary_calendar_does_not_poison_valid_fallback(db):
    """An invalid provider calendar invalidates that provider only."""
    from dataclasses import replace
    from app.hist_options.provider_calendar import ProviderCalendars
    evidence = synthetic_calendars({"DHAN": [EXP], "ANGELONE": [EXP]})
    bad, good = evidence.evidence
    evidence = ProviderCalendars((replace(bad, verification_status="UNVERIFIED"), good))
    quote(db, "DHAN_CHAIN")
    quote(db, "ANGELONE_QUOTE_FULL")
    result = hub.session_option_quotes_result("NIFTY", DAY, as_of=NOW, expiry_dates=evidence)
    assert result["provider"] == "ANGELONE" and result["fallback_reason"] == "CALENDAR_NOT_VERIFIED"


def test_historical_session_cannot_use_today_calendar_without_asof(db):
    """Session aliases require an as-of within that session, never today's discovered date."""
    quote(db, "DHAN_CHAIN")
    result = hub.session_option_quotes_result("NIFTY", DAY, as_of="2026-09-03T04:30:00Z",
                                              expiry_dates=synthetic_calendars({"DHAN": [EXP]}))
    assert result["reason"] == "CALENDAR_SESSION_ASOF_MISMATCH" and not result["rows"]


def test_historical_alias_cannot_bypass_calendar_with_live_discovery_override(db):
    """Provider dates discovered live cannot authorize an as-of alias read."""
    from app.optionchain.resolve import get_chain
    quote(db, "DHAN_CHAIN")
    assert get_chain("NIFTY", "AUTO", at_ts=NOW, db_path=db,
                     provider_expiries={"DHAN": EXP}) is None
    chain = get_chain("NIFTY", EXP, at_ts=NOW, db_path=db,
                      provider_expiries={"DHAN": "15SEP2026"})
    assert chain.expiry == EXP


def test_public_hub_exposes_missing_calendar_reason(db):
    """An unavailable calendar is distinguishable from missing price data."""
    quote(db, "DHAN_CHAIN")
    result = hub.get_chain("NIFTY", as_of=NOW)
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "CALENDAR_EVIDENCE_UNAVAILABLE"
    assert [a["provider"] for a in result["attempts"]] == ["DHAN", "ANGELONE"]


@pytest.mark.parametrize("symbol,exchange", [("NIFTY", "NFO"), ("BANKNIFTY", "NFO"),
    ("SENSEX", "BFO"), ("NATURALGAS", "MCX"), ("NATGASMINI", "MCX"),
    ("CRUDEOIL", "MCX"), ("CRUDEOILM", "MCX")])
@pytest.mark.parametrize("provider,source", [("DHAN", "DHAN_CHAIN"), ("ANGELONE", "ANGELONE_QUOTE_FULL")])
@pytest.mark.parametrize("alias,expected", [("AUTO", EXP), ("NEXT", "15SEP2026")])
def test_provider_calendar_through_captured_chain_adapters(db, symbol, exchange, provider, source, alias, expected):
    """Both captured adapters retain provider calendar identity across NSE/BSE/MCX."""
    from app.optionchain.resolve import get_chain
    for expiry in (EXP, "15SEP2026"):
        quote(db, source, expiry=expiry)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE quote_snapshots SET symbol=?,exchange=?", (symbol, exchange))
    evidence = synthetic_calendars({provider: [EXP, "15SEP2026"]}, underlying=symbol)
    chain = get_chain(symbol, alias, provider=provider, at_ts=NOW, db_path=db, expiry_dates=evidence)
    if exchange == "MCX":
        assert chain is None  # Disabled calendar cannot authorize either captured provider.
        return
    assert chain is not None and chain.expiry == expected
    assert chain.capability["calendar"]["provider"] == provider
    assert chain.capability["calendar"]["expiry"] == ("2026-09-08" if alias == "AUTO" else "2026-09-15")
    assert chain.leg(24000, "CE").provenance["calendar"] == chain.capability["calendar"]


@pytest.mark.parametrize("provider,source", [("DHAN", "DHAN_CHAIN"), ("ANGELONE", "ANGELONE_QUOTE_FULL")])
def test_calendar_does_not_validate_wrong_exchange_capture(db, provider, source):
    """An NSE calendar cannot lend provenance to an MCX-labelled NIFTY capture."""
    from app.optionchain.resolve import get_chain
    quote(db, source)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE quote_snapshots SET exchange='MCX'")
    assert get_chain("NIFTY", "AUTO", provider=provider, at_ts=NOW, db_path=db,
                     expiry_dates=synthetic_calendars({provider: [EXP]})) is None


def test_expired_contract_capture_is_not_eligible(db):
    quote(db, "DHAN_CHAIN", expiry="01SEP2026")
    result = hub.session_option_quotes_result("NIFTY", DAY, expiry="01SEP2026", as_of=NOW)
    assert result["status"] == "UNAVAILABLE" and not result["rows"]


def test_stale_dhan_chain_falls_back_without_borrowing_oi(db):
    quote(db, "DHAN_CHAIN", ts="2026-09-02T04:00:00Z", oi=9000)
    quote(db, "ANGELONE_QUOTE_FULL", oi=None)
    result = hub._hist_chain("NIFTY", DAY, expiry=EXP, as_of=NOW)
    assert result["provider"] == "ANGELONE" and result["fallback_reason"] == "STALE_DATA"
    assert result["chain"][0]["ce_oi"] is None


def test_date_picker_lists_only_sessions_satisfying_the_selected_contract(db):
    """Date discovery cannot advertise another provider's or future-only bars."""
    candle(db, "ANGELONE_CANDLES")
    candle(db, "DHAN_HISTORICAL", ts="2026-09-02T04:35:00Z")
    assert hub.session_dates("NIFTY", provider="DHAN", as_of=NOW) == []
    assert hub.session_dates("NIFTY", as_of=NOW) == [DAY]


def test_optionchain_asof_requires_timezone(db):
    """A naive timestamp cannot fall through to a less strict provider reader."""
    quote(db, "ANGELONE_QUOTE_FULL")
    from app.optionchain.resolve import get_chain
    assert get_chain("NIFTY", EXP, db_path=db, at_ts="2026-09-02T04:30:00") is None


def test_option_series_rejects_ambiguous_instrument_identity(db):
    """One strike/side cannot silently collapse two provider instrument IDs."""
    quote(db, "DHAN_CHAIN")
    quote(db, "DHAN_CHAIN", ts="2026-09-02T04:29:00Z")
    with sqlite3.connect(db) as c:
        c.execute("UPDATE quote_snapshots SET token='other', instrument_key='DHAN:other' "
                  "WHERE received_ts='2026-09-02T04:29:00Z'")
    r = hub.session_option_quotes_result("NIFTY", DAY, provider="DHAN", expiry=EXP, as_of=NOW)
    assert r["status"] == "UNAVAILABLE" and r["reason"] == "AMBIGUOUS_CONTRACT"
