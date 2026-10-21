"""
app/optionchain/history_analytics.py -- Volatility Surface / Straddle P&L /
OI Profile, built on already-captured histcap data only.

Offline: a synthetic market_history.db (same schema/convention as
test_optionchain_api.py's hist_db fixture), no network, no live broker call.
"""
import sqlite3

import pytest

import app.optionchain.api as OCAPI
from app.optionchain.history_analytics import (
    oi_profile,
    straddle_pnl,
    vol_surface,
    volume_spike_ratio,
)


@pytest.fixture
def hist_db(tmp_path, monkeypatch):
    p = tmp_path / "market_history.db"
    con = sqlite3.connect(p)
    con.executescript("""
        CREATE TABLE quote_snapshots(
          id INTEGER PRIMARY KEY, symbol TEXT, kind TEXT, exchange TEXT, expiry TEXT,
          strike REAL, option_type TEXT, ltp REAL, oi REAL, oi_change REAL, volume REAL,
          session_date_ist TEXT, received_ts TEXT, source TEXT DEFAULT 'ANGELONE_QUOTE_FULL');
        CREATE TABLE option_greeks(
          id INTEGER PRIMARY KEY, received_ts TEXT, snap_key TEXT, underlying TEXT, expiry TEXT,
          strike REAL, option_type TEXT, session_date_ist TEXT, iv REAL, broker_status TEXT, source TEXT DEFAULT 'ANGELONE_OPTION_GREEK');
        CREATE TABLE market_candles(
          id INTEGER PRIMARY KEY, symbol TEXT, kind TEXT, tf TEXT, bar_start TEXT,
          o REAL, h REAL, l REAL, c REAL, source TEXT DEFAULT 'ANGELONE_CANDLES',
          expiry TEXT DEFAULT '29SEP2026', exchange TEXT DEFAULT 'NFO', instrument_key TEXT DEFAULT 'NFO:FUT');
    """)

    # --- option_greeks: two expiries, a simple smile shape (PE IV rises as strike falls) ---
    for expiry in ("15SEP2026", "22SEP2026"):
        for k in range(23000, 24001, 100):
            for ot, iv in (("CE", 0.15 + (24000 - k) * 0.00002),
                           ("PE", 0.15 + (k - 23000) * 0.00002)):
                con.execute(
                    "INSERT INTO option_greeks(received_ts,snap_key,underlying,expiry,strike,"
                    "option_type,session_date_ist,iv,broker_status) VALUES(?,?,?,?,?,?,?,?,?)",
                    ("2026-09-09T09:00:01Z", "2026-09-09T09:00:00", "NIFTY", expiry,
                     float(k), ot, "2026-09-09", round(iv, 4), "OK"))

    # --- quote_snapshots: ATM straddle (23500) premium decaying/moving over 3 ticks ---
    exp = "15SEP2026"
    for i, (rts, ce, pe) in enumerate([
        ("2026-09-09T09:00:00Z", 120.0, 110.0),
        ("2026-09-09T09:05:00Z", 100.0, 130.0),
        ("2026-09-09T09:10:00Z", 90.0, 145.0),
    ]):
        con.execute("INSERT INTO quote_snapshots(symbol,kind,expiry,strike,option_type,ltp,oi,"
                    "session_date_ist,received_ts) VALUES('NIFTY','OPTION',?,?,'CE',?,?,?,?)",
                    (exp, 23500.0, ce, 50000.0 + i * 1000, "2026-09-09", rts))
        con.execute("INSERT INTO quote_snapshots(symbol,kind,expiry,strike,option_type,ltp,oi,"
                    "session_date_ist,received_ts) VALUES('NIFTY','OPTION',?,?,'PE',?,?,?,?)",
                    (exp, 23500.0, pe, 60000.0 + i * 500, "2026-09-09", rts))
    con.execute("INSERT INTO quote_snapshots(symbol,kind,ltp,session_date_ist,received_ts) "
                "VALUES('NIFTY','INDEX',23505.0,'2026-09-09','2026-09-09T09:00:00Z')")

    # --- market_candles: a few 1m futures bars for the OI-profile underlying overlay ---
    for i in range(3):
        con.execute("INSERT INTO market_candles(symbol,kind,tf,bar_start,o,h,l,c) "
                    "VALUES('NIFTY','FUTURE','1m',?,?,?,?,?)",
                    (f"2026-09-09T09:0{i}:00Z", 23500 + i, 23510 + i, 23490 + i, 23505 + i))
    con.commit()
    con.close()
    monkeypatch.setenv("CHANAKYA_HIST_DB_PATH", str(p))
    OCAPI._hist_cache.clear()
    yield str(p)
    OCAPI._hist_cache.clear()


# --------------------------------------------------------------------------- #
#  Volatility Surface                                                         #
# --------------------------------------------------------------------------- #
def test_vol_surface_covers_both_captured_expiries(hist_db):
    vs = vol_surface("NIFTY")
    assert vs.status == "ok"
    assert set(vs.expiries) == {"15SEP2026", "22SEP2026"}
    assert len(vs.points) == 2 * 2 * 11               # 2 expiries x (CE+PE) x 11 strikes
    # smile shape survived: PE IV should be higher at a low strike than a high strike
    pe_low = next(p for p in vs.points if p["expiry"] == "15SEP2026"
                  and p["strike"] == 23000 and p["option_type"] == "PE")
    pe_high = next(p for p in vs.points if p["expiry"] == "15SEP2026"
                   and p["strike"] == 24000 and p["option_type"] == "PE")
    assert pe_low["iv"] < pe_high["iv"]


def test_vol_surface_respects_max_expiries(hist_db):
    vs = vol_surface("NIFTY", max_expiries=1)
    assert vs.status == "ok"
    assert len(vs.expiries) == 1


def test_vol_surface_no_data_for_unknown_underlying(hist_db):
    vs = vol_surface("BANKNIFTY")
    assert vs.status == "no_data"


# --------------------------------------------------------------------------- #
#  Straddle P&L                                                               #
# --------------------------------------------------------------------------- #
def test_straddle_pnl_resolves_atm_and_tracks_premium(hist_db):
    sp = straddle_pnl("NIFTY", "15SEP2026")
    assert sp.status == "ok"
    assert sp.strike == 23500.0
    assert len(sp.series) == 3
    assert sp.series[0]["straddle"] == 230.0          # 120 + 110
    assert sp.series[0]["pnl"] == 0.0
    assert sp.series[-1]["straddle"] == 235.0         # 90 + 145
    assert sp.series[-1]["pnl"] == 5.0


def test_straddle_pnl_explicit_strike_overrides_atm_resolution(hist_db):
    sp = straddle_pnl("NIFTY", "15SEP2026", strike=23500.0)
    assert sp.status == "ok" and sp.strike == 23500.0


def test_straddle_pnl_no_data_for_missing_expiry(hist_db):
    sp = straddle_pnl("NIFTY", "29SEP2026")
    assert sp.status == "no_data"


# --------------------------------------------------------------------------- #
#  OI Profile                                                                 #
# --------------------------------------------------------------------------- #
def test_oi_profile_returns_strikes_series_and_underlying_candles(hist_db):
    op = oi_profile("NIFTY", "15SEP2026", top_n_strikes=5)
    assert op.status == "ok"
    assert 23500.0 in op.strikes
    assert len(op.oi_series) > 0
    assert all("ce_oi" in row or "pe_oi" in row for row in op.oi_series)
    assert len(op.underlying_candles) == 3
    assert op.underlying_candles[0]["c"] == 23505


def test_oi_profile_no_data_for_missing_expiry(hist_db):
    op = oi_profile("NIFTY", "29SEP2026")
    assert op.status == "no_data"


# --------------------------------------------------------------------------- #
#  API route handlers (direct call, repo convention -- no TestClient)         #
# --------------------------------------------------------------------------- #
def test_api_vol_surface_route(hist_db):
    j = OCAPI.api_vol_surface("nifty")
    assert j["status"] == "ok"
    assert len(j["points"]) > 0
    import json
    json.dumps(j)


def test_api_straddle_pnl_route(hist_db):
    j = OCAPI.api_straddle_pnl("nifty", "15SEP2026")
    assert j["status"] == "ok" and j["strike"] == 23500.0


def test_api_oi_profile_route(hist_db):
    j = OCAPI.api_oi_profile("nifty", "15SEP2026")
    assert j["status"] == "ok"


# --------------------------------------------------------------------------- #
#  Volume Spike Ratio -- dedicated fixture (the shared hist_db fixture above   #
#  never sets `volume` on its quote_snapshots rows, so it correctly reads as   #
#  "no_data" for this function -- a separate, minimal DB is clearer than       #
#  overloading the shared one).                                                #
# --------------------------------------------------------------------------- #
@pytest.fixture
def vol_hist_db(tmp_path, monkeypatch):
    p = tmp_path / "market_history_vol.db"
    con = sqlite3.connect(p)
    con.executescript("""
        CREATE TABLE quote_snapshots(
          id INTEGER PRIMARY KEY, symbol TEXT, kind TEXT, expiry TEXT,
          strike REAL, option_type TEXT, volume REAL, session_date_ist TEXT, received_ts TEXT, source TEXT DEFAULT 'ANGELONE_QUOTE_FULL');
    """)
    # Three real captured sessions, cumulative-intraday volume (several polls
    # per session, only the MAX per strike/option_type counts, same
    # convention oi_history_adapter's vol_delta logic already documents).
    sessions = {
        "2026-09-08": [(23500.0, "CE", 1000.0), (23500.0, "CE", 1200.0), (23500.0, "PE", 900.0)],
        "2026-09-09": [(23500.0, "CE", 1100.0), (23500.0, "PE", 1000.0)],
        "2026-09-10": [(23500.0, "CE", 4000.0), (23500.0, "PE", 3500.0)],   # today -- a real volume spike
    }
    for session, rows in sessions.items():
        for strike, ot, vol in rows:
            con.execute("INSERT INTO quote_snapshots(symbol,kind,expiry,strike,option_type,volume,"
                        "session_date_ist,received_ts) VALUES('NIFTY','OPTION','15SEP2026',?,?,?,?,?)",
                        (strike, ot, vol, session, f"{session}T09:00:00Z"))
    con.commit()
    con.close()
    monkeypatch.setenv("CHANAKYA_HIST_DB_PATH", str(p))
    OCAPI._hist_cache.clear()
    yield str(p)
    OCAPI._hist_cache.clear()


def test_volume_spike_ratio_flags_a_real_spike(vol_hist_db):
    r = volume_spike_ratio("NIFTY")
    assert r.status == "ok"
    assert r.today_session == "2026-09-10"
    assert r.today_volume == 4000.0 + 3500.0            # MAX per (strike, option_type), summed
    assert r.avg_volume == (1200.0 + 900.0 + 1100.0 + 1000.0) / 2   # mean of the 2 prior real sessions
    assert r.ratio > 3.0                                 # a genuine spike vs the prior average
    assert r.n_days_in_average == 2                     # honestly reports how thin the real history is


def test_volume_spike_ratio_no_data_for_unknown_symbol(vol_hist_db):
    r = volume_spike_ratio("BANKNIFTY")
    assert r.status == "no_data"


def test_volume_spike_ratio_insufficient_history_is_not_a_crash(vol_hist_db):
    """Only ONE real session captured -- nothing to average against."""
    p = vol_hist_db
    con = sqlite3.connect(p)
    con.execute("DELETE FROM quote_snapshots WHERE session_date_ist != '2026-09-10'")
    con.commit()
    con.close()
    r = volume_spike_ratio("NIFTY", db_path=p)
    assert r.status == "insufficient_history"
    assert r.today_volume == 4000.0 + 3500.0


def test_history_readers_use_one_provider_and_preserve_provenance(hist_db):
    """Each analytical result selects Dhan, or a whole AngelOne fallback."""
    with sqlite3.connect(hist_db) as c:
        for side in ('CE', 'PE'):
            c.execute("INSERT INTO quote_snapshots(symbol,kind,expiry,strike,option_type,ltp,oi,"
                "received_ts,source) VALUES ('NIFTY','OPTION','15SEP2026',23500,?,300,7000,"
                "'2026-09-09T09:00:00Z','DHAN_CHAIN')", (side,))
    r = straddle_pnl("NIFTY", "15SEP2026", strike=23500, as_of="2026-09-09T09:10:00Z")
    assert r.entry_premium == 600 and r.provenance["provider"] == "DHAN"
    assert {x["provider"] for x in r.series} == {"DHAN"}
    a = straddle_pnl("NIFTY", "15SEP2026", strike=23500, provider="ANGELONE")
    assert a.entry_premium == 230 and not a.provenance["fallback_used"]
    surface = vol_surface("NIFTY", provider="DHAN")
    assert surface.status == "no_data"
    surface = vol_surface("NIFTY")
    assert surface.provenance["provider"] == "ANGELONE" and surface.provenance["fallback_used"]
    profile = oi_profile("NIFTY", "15SEP2026")
    assert profile.oi_series[0]["ce_oi"] == 7000
    assert profile.underlying_candles == []  # no Dhan candles: no AngelOne substitution


def test_history_atm_cannot_use_future_underlying_or_future_only_strike(hist_db):
    """Entry-time ATM selection has no permission to look ahead."""
    with sqlite3.connect(hist_db) as c:
        c.execute("UPDATE quote_snapshots SET received_ts='2026-09-09T09:00:01Z' WHERE kind='INDEX'")
    r = straddle_pnl("NIFTY", "15SEP2026", entry_ts="2026-09-09T09:00:00Z")
    assert r.status == "no_data" and r.entry_premium is None


def test_history_explicit_entry_requires_observation_at_or_before_entry(hist_db):
    """A future-only premium must not become the entry premium."""
    r = straddle_pnl("NIFTY", "15SEP2026", strike=23500, entry_ts="2026-09-09T08:59:00Z")
    assert r.status == "no_data" and r.entry_premium is None


def test_volume_aggregate_keeps_expiry_identity(vol_hist_db):
    """The documented whole-chain aggregate sums contracts; it cannot collapse expiries by strike."""
    with sqlite3.connect(vol_hist_db) as c:
        c.execute("INSERT INTO quote_snapshots(symbol,kind,expiry,strike,option_type,volume,"
                  "session_date_ist,received_ts) VALUES ('NIFTY','OPTION','22SEP2026',23500,'CE',500,"
                  "'2026-09-10','2026-09-10T09:00:00Z')")
    r = volume_spike_ratio("NIFTY")
    assert r.today_volume == 8000
    assert r.expiries == ["15SEP2026", "22SEP2026"]


def test_history_api_cache_is_provider_and_time_specific(hist_db):
    """Cached AngelOne history cannot satisfy an explicit Dhan or earlier query."""
    a = OCAPI.api_straddle_pnl("NIFTY", "15SEP2026", strike=23500,
                              provider="ANGELONE", as_of="2026-09-09T09:05:00Z")
    assert a["status"] == "ok" and len(a["series"]) == 2
    d = OCAPI.api_straddle_pnl("NIFTY", "15SEP2026", strike=23500,
                              provider="DHAN", as_of="2026-09-09T09:05:00Z")
    assert d["status"] == "no_data"
    past = OCAPI.api_straddle_pnl("NIFTY", "15SEP2026", strike=23500,
                                 provider="ANGELONE", as_of="2026-09-09T08:59:00Z")
    assert past["status"] == "no_data"


def test_history_surface_excludes_future_provider_capture(hist_db):
    """A future Dhan IV capture cannot beat valid past AngelOne observations."""
    with sqlite3.connect(hist_db) as c:
        c.execute("INSERT INTO option_greeks(underlying,expiry,strike,option_type,iv,received_ts,"
                  "snap_key,broker_status,source) VALUES ('NIFTY','15SEP2026',23500,'CE',0.9,"
                  "'2026-09-09T09:02:00Z','2026-09-09T09:02:00','OK','DHAN_OPTION_GREEK')")
    r = vol_surface("NIFTY", as_of="2026-09-09T09:01:00Z")
    assert r.provenance["provider"] == "ANGELONE" and all(p["iv"] < 0.9 for p in r.points)
    assert vol_surface("NIFTY", as_of="2026-09-09T09:01:00Z", provider="DHAN").status == "no_data"


def test_history_rejects_captures_after_contract_expiry(hist_db):
    """Late captures of a dead contract cannot become a later premium series."""
    with sqlite3.connect(hist_db) as c:
        c.execute("UPDATE quote_snapshots SET received_ts='2026-09-16T09:00:00Z' WHERE kind='OPTION'")
    r = straddle_pnl("NIFTY", "15SEP2026", strike=23500, as_of="2026-09-16T09:01:00Z")
    assert r.status == "no_data"


@pytest.mark.parametrize("bad_oi", [-1, float('inf')])
def test_invalid_primary_oi_cannot_mask_valid_fallback(hist_db, bad_oi):
    """An empty profile after validation is unavailable, not a successful primary result."""
    with sqlite3.connect(hist_db) as c:
        c.execute("INSERT INTO quote_snapshots(symbol,kind,expiry,strike,option_type,oi,received_ts,source) "
                  "VALUES ('NIFTY','OPTION','15SEP2026',23500,'CE',?,'2026-09-09T09:00:00Z','DHAN_CHAIN')", (bad_oi,))
    r = oi_profile('NIFTY', '15SEP2026')
    assert r.status == 'ok' and r.provenance['provider'] == 'ANGELONE'


def test_invalid_entry_quote_cannot_move_entry_into_the_future(hist_db):
    """Reject a bad entry instead of silently starting at a later valid premium."""
    with sqlite3.connect(hist_db) as c:
        c.execute("UPDATE quote_snapshots SET ltp=-1 WHERE kind='OPTION' AND option_type='CE' "
                  "AND received_ts='2026-09-09T09:00:00Z'")
    r = straddle_pnl('NIFTY', '15SEP2026', strike=23500, entry_ts='2026-09-09T09:00:00Z')
    assert r.status == 'no_data' and r.entry_premium is None


def test_entry_selection_precedes_series_limit(hist_db):
    """Old captures must not consume the limit before selecting the requested entry."""
    r = straddle_pnl('NIFTY', '15SEP2026', strike=23500, entry_ts='2026-09-09T09:10:00Z', limit=2)
    assert r.entry_ts == '2026-09-09T09:10:00Z'
    assert r.entry_premium == 235
