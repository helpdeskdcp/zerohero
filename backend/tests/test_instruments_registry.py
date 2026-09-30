"""app.instruments.resolve() -- the friendly-name -> {exchange, symboltoken}
registry every capture/quote path resolves through -- plus
list_nse_option_expiries(), the AUTO-expiry resolver app.optionchain.api
calls before ever fetching a chain."""
from datetime import datetime, timedelta, timezone

from app import instruments


def test_resolve_indiavix_is_nse_amxidx():
    # MCP 3.3 data-foundation gap: India VIX capture. Token verified against
    # the cached AngelOne instrument master (data/instrument_master.json):
    # {"token": "99926017", "symbol": "India VIX", "name": "INDIA VIX",
    #  "instrumenttype": "AMXIDX", "exch_seg": "NSE"} -- never guessed.
    meta = instruments.resolve("INDIAVIX")
    assert meta is not None
    assert meta["exchange"] == "NSE"
    assert meta["symboltoken"] == "99926017"


def test_resolve_indiavix_aliases():
    for alias in ("India VIX", "NIFTY VIX", "indiavix"):
        meta = instruments.resolve(alias)
        assert meta is not None and meta["symboltoken"] == "99926017"


def test_indiavix_is_not_an_mcx_symbol():
    # histcap.worker._EXCH_OF defaults to "NSE" for anything not listed --
    # INDIAVIX must never be routed down the MCX (futures-first) capture path.
    from app.histcap.worker import _EXCH_OF
    assert _EXCH_OF.get("INDIAVIX", "NSE") == "NSE"


# ---------------------------------------------------------------- list_nse_option_expiries
# Found live 2026-09-30: app.optionchain.api's AUTO-expiry resolution
# (list_nse_option_expiries) had ZERO direct test coverage before this --
# only resolve_nse_option()'s own AUTO path (a separate function) had a
# chronological-vs-lexical regression test. The real incident that day was
# an operationally stale master cache (fixed via scripts/
# refresh_instrument_master_cron.sh, a daily refresh -- the cache was
# never refreshed automatically before, only downloaded once on first
# use), not a bug in this function's own date logic -- but that logic had
# never been locked in by a test either, so it's added here now.

def _fake_row(underlying: str, expiry: str, *, token: str, option_type: str = "CE") -> dict:
    return {"exch_seg": "NFO", "name": underlying, "token": token,
            "symbol": f"{underlying}{expiry}22000{option_type}", "expiry": expiry, "strike": "2200000"}


def test_list_nse_option_expiries_sorts_chronologically_not_lexically(monkeypatch):
    import calendar

    base = datetime.now(timezone.utc)

    def _months_out(n: int, day: int) -> datetime:
        y, m = base.year, base.month + n
        y += (m - 1) // 12
        m = (m - 1) % 12 + 1
        day = min(day, calendar.monthrange(y, m)[1])
        return datetime(y, m, day, tzinfo=timezone.utc)

    nearer = _months_out(2, 28)     # e.g. "28NOV2026" -- chronologically first
    farther = _months_out(3, 3)     # e.g. "03DEC2026" -- second, but "03" < "28" as plain text
    e_near, e_far = nearer.strftime("%d%b%Y").upper(), farther.strftime("%d%b%Y").upper()
    assert nearer < farther

    monkeypatch.setattr(instruments, "master_rows", lambda **_k: [
        _fake_row("NIFTY", e_far, token="2"), _fake_row("NIFTY", e_near, token="1")])
    r = instruments.list_nse_option_expiries("NIFTY")
    assert r["status"] == "OK"
    assert r["current_expiry"] == e_near     # never e_far, even though "e_far" < "e_near" lexically
    assert r["next_expiry"] == e_far
    assert r["available_expiries"] == [e_near, e_far]


def test_list_nse_option_expiries_excludes_expired(monkeypatch):
    past = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%d%b%Y").upper()
    future = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%d%b%Y").upper()
    monkeypatch.setattr(instruments, "master_rows", lambda **_k: [
        _fake_row("NIFTY", past, token="1"), _fake_row("NIFTY", future, token="2")])
    r = instruments.list_nse_option_expiries("NIFTY")
    assert r["status"] == "OK"
    assert r["current_expiry"] == future
    assert past not in r["available_expiries"]


def test_list_nse_option_expiries_contract_invalid_when_all_expired(monkeypatch):
    past = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%d%b%Y").upper()
    monkeypatch.setattr(instruments, "master_rows", lambda **_k: [_fake_row("NIFTY", past, token="1")])
    r = instruments.list_nse_option_expiries("NIFTY")
    assert r["status"] == "CONTRACT_INVALID"


def test_list_nse_option_expiries_data_unavailable_when_no_nfo_rows(monkeypatch):
    monkeypatch.setattr(instruments, "master_rows", lambda **_k: [])
    r = instruments.list_nse_option_expiries("NIFTY")
    assert r["status"] == "DATA_UNAVAILABLE"
