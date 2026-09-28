"""app.connectors.dhan_instruments -- daily front-month future + ATM CE/PE
resolution against a (fake, in-memory) Dhan scrip-master CSV shape. Never
downloads the real master; every test injects `rows=` directly."""
from __future__ import annotations

from datetime import date, timedelta

from app.connectors import dhan_instruments as di

TODAY = di.today_ist()
NEXT_WEEK = (TODAY + timedelta(days=7)).isoformat()
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()
FAR_FUTURE = (TODAY + timedelta(days=40)).isoformat()


def _fut_row(symbol, sec_id, expiry, instrument="FUTIDX", exch="NSE", seg="D"):
    # SM_SYMBOL_NAME deliberately omitted (blank) -- matches the real Dhan
    # master's NSE F&O rows; see test_resolve_front_month_future_matches_even_with_blank_sm_symbol_name.
    return {"SEM_EXM_EXCH_ID": exch, "SEM_SEGMENT": seg, "SEM_SMST_SECURITY_ID": sec_id,
            "SEM_INSTRUMENT_NAME": instrument,
            "SEM_EXPIRY_DATE": f"{expiry} 14:30:00", "SEM_TRADING_SYMBOL": f"{symbol}-FUT"}


def _opt_row(symbol, sec_id, expiry, strike, opt_type, exch="NSE", seg="D"):
    return {"SEM_EXM_EXCH_ID": exch, "SEM_SEGMENT": seg, "SEM_SMST_SECURITY_ID": sec_id,
            "SEM_INSTRUMENT_NAME": "OPTIDX",
            "SEM_EXPIRY_DATE": f"{expiry} 14:30:00", "SEM_STRIKE_PRICE": str(strike),
            "SEM_OPTION_TYPE": opt_type, "SEM_TRADING_SYMBOL": f"{symbol}-{strike}-{opt_type}"}


# ---------------------------------------------------------------------------
# resolve_front_month_future
# ---------------------------------------------------------------------------

def test_resolve_front_month_future_picks_nearest_not_yet_expired():
    rows = [
        _fut_row("NIFTY", "1", YESTERDAY),          # expired -- must be excluded
        _fut_row("NIFTY", "2", NEXT_WEEK),          # nearest valid
        _fut_row("NIFTY", "3", FAR_FUTURE),
        _fut_row("BANKNIFTY", "9", NEXT_WEEK),      # different symbol -- must be excluded
    ]
    out = di.resolve_front_month_future("NIFTY", rows=rows)
    assert out["security_id"] == "2" and out["expiry"] == NEXT_WEEK


def test_resolve_front_month_future_none_when_nothing_matches():
    assert di.resolve_front_month_future("NIFTY", rows=[_fut_row("BANKNIFTY", "1", NEXT_WEEK)]) is None


def test_resolve_front_month_future_ignores_expired_only_rows():
    assert di.resolve_front_month_future("NIFTY", rows=[_fut_row("NIFTY", "1", YESTERDAY)]) is None


def test_resolve_front_month_future_wrong_exchange_segment_excluded():
    rows = [_fut_row("NIFTY", "1", NEXT_WEEK, exch="BSE")]
    assert di.resolve_front_month_future("NIFTY", exch_id="NSE", rows=rows) is None


def test_resolve_front_month_future_matches_even_with_blank_sm_symbol_name():
    """Regression: Dhan's real published scrip master leaves SM_SYMBOL_NAME
    BLANK for every NSE F&O row (verified live 2026-09-28, e.g. the real
    NIFTY-Sep2026-FUT row) -- only MCX rows populate it. Matching must go by
    SEM_TRADING_SYMBOL's "{symbol}-" prefix, which is reliably present on
    every row, or every NSE index future/option silently fails to resolve."""
    row = {"SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "D", "SEM_SMST_SECURITY_ID": "68407",
           "SEM_INSTRUMENT_NAME": "FUTIDX", "SM_SYMBOL_NAME": "",  # blank, exactly like the real file
           "SEM_EXPIRY_DATE": f"{NEXT_WEEK} 14:30:00", "SEM_TRADING_SYMBOL": "NIFTY-Sep2026-FUT"}
    out = di.resolve_front_month_future("NIFTY", rows=[row])
    assert out is not None and out["security_id"] == "68407"


def test_resolve_front_month_future_prefix_match_does_not_leak_across_similarly_named_symbols():
    """NIFTYFPI is a real, separate, listed contract -- "NIFTY" must match
    only the "NIFTY-" prefix, never "NIFTYFPI-"."""
    rows = [_fut_row("NIFTYFPI", "1", NEXT_WEEK)]
    assert di.resolve_front_month_future("NIFTY", rows=rows) is None


# ---------------------------------------------------------------------------
# resolve_atm_options
# ---------------------------------------------------------------------------

def test_resolve_atm_options_rounds_spot_to_the_strike_step_and_picks_nearest_expiry():
    rows = [
        _opt_row("NIFTY", "10", NEXT_WEEK, 22750, "CE"), _opt_row("NIFTY", "11", NEXT_WEEK, 22750, "PE"),
        _opt_row("NIFTY", "12", NEXT_WEEK, 22800, "CE"), _opt_row("NIFTY", "13", NEXT_WEEK, 22800, "PE"),
        _opt_row("NIFTY", "14", FAR_FUTURE, 22800, "CE"), _opt_row("NIFTY", "15", FAR_FUTURE, 22800, "PE"),
    ]
    out = di.resolve_atm_options("NIFTY", 22780.25, rows=rows)  # nearest 50-multiple -> 22800
    assert out["strike"] == 22800.0 and out["expiry"] == NEXT_WEEK
    assert out["ce"]["security_id"] == "12" and out["pe"]["security_id"] == "13"


def test_resolve_atm_options_falls_back_to_nearest_available_strike():
    """The exact rounded strike isn't listed (e.g. deep contract not yet
    seeded) -- pick whatever's actually closest instead of failing."""
    rows = [_opt_row("NIFTY", "1", NEXT_WEEK, 22700, "CE"), _opt_row("NIFTY", "2", NEXT_WEEK, 22700, "PE")]
    out = di.resolve_atm_options("NIFTY", 22780.25, rows=rows)  # target=22800, only 22700 exists
    assert out["strike"] == 22700.0


def test_resolve_atm_options_none_for_a_symbol_with_no_configured_strike_step():
    rows = [_opt_row("MIDCPNIFTY", "1", NEXT_WEEK, 100, "CE"), _opt_row("MIDCPNIFTY", "2", NEXT_WEEK, 100, "PE")]
    assert di.resolve_atm_options("MIDCPNIFTY", 100.0, rows=rows) is None


def test_resolve_atm_options_none_for_missing_or_zero_spot():
    rows = [_opt_row("NIFTY", "1", NEXT_WEEK, 22800, "CE"), _opt_row("NIFTY", "2", NEXT_WEEK, 22800, "PE")]
    assert di.resolve_atm_options("NIFTY", 0, rows=rows) is None
    assert di.resolve_atm_options("NIFTY", None, rows=rows) is None


def test_resolve_atm_options_none_when_one_leg_is_missing():
    rows = [_opt_row("NIFTY", "1", NEXT_WEEK, 22800, "CE")]  # PE never seeded
    assert di.resolve_atm_options("NIFTY", 22800, rows=rows) is None


def test_resolve_atm_options_excludes_expired_expiries():
    rows = [_opt_row("NIFTY", "1", YESTERDAY, 22800, "CE"), _opt_row("NIFTY", "2", YESTERDAY, 22800, "PE"),
            _opt_row("NIFTY", "3", NEXT_WEEK, 22800, "CE"), _opt_row("NIFTY", "4", NEXT_WEEK, 22800, "PE")]
    out = di.resolve_atm_options("NIFTY", 22800, rows=rows)
    assert out["expiry"] == NEXT_WEEK


# ---------------------------------------------------------------------------
# resolve_daily_instruments -- the combined per-symbol driver
# ---------------------------------------------------------------------------

def test_resolve_daily_instruments_builds_future_plus_atm_pair(monkeypatch):
    rows = [
        _fut_row("NIFTY", "100", NEXT_WEEK),
        _opt_row("NIFTY", "101", NEXT_WEEK, 22800, "CE"), _opt_row("NIFTY", "102", NEXT_WEEK, 22800, "PE"),
    ]
    monkeypatch.setattr(di, "_load_master", lambda force=False: rows)
    out, meta, incomplete = di.resolve_daily_instruments(["NIFTY"], lambda sym: 22780.25)
    ids = {i["security_id"] for i in out}
    assert ids == {"100", "101", "102"}
    assert all(i["exchange_segment"] == "NSE_FNO" for i in out)
    assert meta["100"]["kind"] == "FUTURE" and meta["101"]["kind"] == "OPTION_CE"
    assert incomplete == []


def test_resolve_daily_instruments_skips_a_symbol_with_no_spot_without_crashing(monkeypatch):
    rows = [_fut_row("NIFTY", "100", NEXT_WEEK)]
    monkeypatch.setattr(di, "_load_master", lambda force=False: rows)
    out, meta, incomplete = di.resolve_daily_instruments(["NIFTY"], lambda sym: None)  # spot lookup failed
    assert {i["security_id"] for i in out} == {"100"}  # future still resolved, ATM just skipped
    assert incomplete == ["NIFTY"]  # caller should retry -- this is NOT a "done for the day" result


def test_resolve_daily_instruments_one_bad_symbol_does_not_block_the_rest(monkeypatch):
    rows = [_fut_row("NIFTY", "100", NEXT_WEEK), _fut_row("BANKNIFTY", "200", NEXT_WEEK)]
    monkeypatch.setattr(di, "_load_master", lambda force=False: rows)

    def flaky_spot(sym):
        if sym == "NIFTY":
            raise RuntimeError("boom")
        return 54500.0

    out, meta, incomplete = di.resolve_daily_instruments(["NIFTY", "BANKNIFTY"], flaky_spot)
    ids = {i["security_id"] for i in out}
    assert "100" in ids and "200" in ids  # NIFTY's spot blew up but its future still resolved
    assert incomplete == ["NIFTY", "BANKNIFTY"]  # neither got a full ATM pair (no options in these rows)


def test_today_ist_is_a_date_object():
    assert isinstance(di.today_ist(), date)
