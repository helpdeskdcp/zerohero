"""app.optionchain.sources.dhan_chain -- pure parse() tests on canned JSON
shaped exactly like the real Dhan v2 /optionchain response (verified live
2026-09-30: NIFTY 06OCT2026, 238 real strikes, 100% Greek coverage). No
network, no auth -- fetch()'s HTTP/auth path is exercised only by the
live verification, not the automated suite."""
import pytest
from datetime import datetime, timezone

from app.optionchain.sources import dhan_chain as dc

# Trimmed, real-shaped canned response (field names/nesting exactly as
# Dhan's live API returned for NIFTY 06OCT2026 -- see dhan_chain.py's own
# docstring for the verification date).
_REAL_SHAPED = {
    "status": "success",
    "data": {
        "last_price": 22763.9,
        "oc": {
            "22750.000000": {
                "ce": {"last_price": 166.95, "oi": 5670795, "previous_oi": 758355,
                      "volume": 88082215, "top_bid_price": 166.95, "top_ask_price": 167.35,
                      "top_bid_quantity": 390, "top_ask_quantity": 780,
                      "implied_volatility": 11.503434261868377,
                      "greeks": {"delta": 0.56606, "gamma": 0.00114, "theta": -14.17331, "vega": 11.82855}},
                "pe": {"last_price": 148.3, "oi": 3200000, "previous_oi": 3500000,
                      "volume": 50000000, "top_bid_price": 148.1, "top_ask_price": 148.5,
                      "top_bid_quantity": 200, "top_ask_quantity": 300,
                      "implied_volatility": 12.1,
                      "greeks": {"delta": -0.4, "gamma": 0.001, "theta": -10.0, "vega": 9.0}},
            },
            "23400.000000": {
                "ce": {"last_price": 7.25, "oi": 5167305, "previous_oi": 3463070,
                      "volume": 16329430, "top_bid_price": 7.2, "top_ask_price": 7.3,
                      "top_bid_quantity": 44200, "top_ask_quantity": 41080,
                      "implied_volatility": 12.249246498755785, "security_id": 40755,
                      "greeks": {"delta": 0.0478, "gamma": 0.00027, "theta": -3.16948, "vega": 2.98735}},
                "pe": {"last_price": 644.0, "oi": 658970, "previous_oi": 672295,
                      "volume": 159380, "top_bid_price": 641.4, "top_ask_price": 644.15,
                      "top_bid_quantity": 65, "top_ask_quantity": 65,
                      "implied_volatility": 15.155583734516354,
                      "greeks": {"delta": -0.9108, "gamma": 0.00035, "theta": 0.0846, "vega": 4.84347}},
            },
        },
    },
}


def test_parse_real_shaped_response():
    c = dc.parse(_REAL_SHAPED, "NIFTY", "06OCT2026")
    assert c is not None and c.source == "dhan_chain"
    assert c.spot == 22763.9
    assert len(c.rows) == 2

    ce = c.leg(23400, "CE")
    assert ce.ltp == 7.25 and ce.bid == 7.2 and ce.ask == 7.3
    assert ce.bid_qty == 44200 and ce.ask_qty == 41080
    assert ce.oi == 5167305 and ce.oi_change == pytest.approx(5167305 - 3463070)
    assert ce.iv == pytest.approx(0.12249246498755785)      # normalised from percent
    assert ce.delta == pytest.approx(0.0478) and ce.gamma == pytest.approx(0.00027)
    assert ce.theta == pytest.approx(-3.16948) and ce.vega == pytest.approx(2.98735)
    assert ce.security_id == "40755"      # real instrument identity, for capture dedup/order-routing


def test_parse_security_id_is_none_when_absent():
    pe = dc.parse(_REAL_SHAPED, "NIFTY", "06OCT2026").leg(23400, "PE")
    assert pe.security_id is None    # fixture's PE leg has no security_id -- never fabricated


def test_parse_has_full_greek_coverage_capability():
    c = dc.parse(_REAL_SHAPED, "NIFTY", "06OCT2026")
    assert c.capability == {"has_greeks": True, "greek_coverage": 1.0, "has_iv": True,
                            "has_oi": True, "has_oi_change": True, "spot_source": "dhan"}


def test_parse_computes_atm_from_real_spot():
    c = dc.parse(_REAL_SHAPED, "NIFTY", "06OCT2026")
    assert c.atm_strike == 22750.0    # nearest of {22750, 23400} to spot 22763.9


def test_parse_bad_status_returns_none():
    assert dc.parse({"status": "failed", "data": {}}, "NIFTY", "06OCT2026") is None


def test_parse_empty_chain_returns_none():
    assert dc.parse({"status": "success", "data": {"last_price": 100.0, "oc": {}}},
                    "NIFTY", "06OCT2026") is None


def test_underlying_scrip_ids_are_from_the_real_dhan_master():
    # Verified live 2026-09-30 against app.connectors.dhan_instruments'
    # real cached master -- never guessed.
    assert dc._UNDERLYING_SCRIP == {"NIFTY": 13, "BANKNIFTY": 25, "FINNIFTY": 27,
                                    "MIDCPNIFTY": 442, "SENSEX": 51, "BANKEX": 69}


def test_fetch_returns_none_for_unsupported_underlying():
    assert dc.fetch("GOLD", "06OCT2026") is None    # not a Dhan-chain underlying we support


def test_fetch_returns_none_without_credentials(monkeypatch):
    monkeypatch.setattr(dc, "_headers", lambda: None)
    assert dc.fetch("NIFTY", "06OCT2026") is None


def test_fetch_raw_returns_none_without_credentials(monkeypatch):
    monkeypatch.setattr(dc, "_headers", lambda: None)
    assert dc.fetch_raw("NIFTY", "06OCT2026") is None


def test_fetch_uses_fetch_raw_for_a_single_network_call(monkeypatch):
    calls = {"n": 0}

    def _fake_raw(u, e, **kw):
        calls["n"] += 1
        return _REAL_SHAPED

    monkeypatch.setattr(dc, "fetch_raw", _fake_raw)
    c = dc.fetch("NIFTY", "06OCT2026")
    assert c is not None and calls["n"] == 1     # exactly one raw call, not two


def test_fetch_raw_response_never_contains_request_auth_headers(monkeypatch):
    # Sanitization guarantee (MCP 3.3 continuation item 11): fetch_raw()
    # returns ONLY the response body -- the request's auth headers
    # (access-token/client-id) are never passed back to any caller, so
    # anything archived from this function's return value can never leak
    # a credential, by construction, not by a filter that could miss one.
    class _FakeResp:
        status_code = 200

        def json(self):
            return _REAL_SHAPED

    class _FakeSession:
        def post(self, url, json, headers, timeout):
            # capture what fetch_raw sent us, to prove it never returns it
            self.sent_headers = headers
            return _FakeResp()

    monkeypatch.setattr(dc, "_headers", lambda: {"access-token": "SECRET_TOKEN", "client-id": "SECRET_CID"})
    sess = _FakeSession()
    raw = dc.fetch_raw("NIFTY", "06OCT2026", session=sess)
    dumped = str(raw)
    assert "SECRET_TOKEN" not in dumped and "SECRET_CID" not in dumped
    assert sess.sent_headers == {"access-token": "SECRET_TOKEN", "client-id": "SECRET_CID"}  # sent, never returned


def test_to_iso_date_accepts_canonical_and_iso():
    assert dc._to_iso_date("06OCT2026") == "2026-10-06"
    assert dc._to_iso_date("2026-10-06") == "2026-10-06"
    assert dc._to_iso_date("not-a-date") is None


def test_to_canonical_expiry_round_trips():
    assert dc._to_canonical_expiry("2026-10-06") == "06OCT2026"


def test_mcx_underlying_resolves_front_month_future(monkeypatch):
    from app.connectors import dhan_instruments as di
    monkeypatch.setattr(di, "resolve_front_month_future",
                        lambda sym, **k: {"security_id": "569900", "expiry": "2026-10-19"})
    assert dc._underlying("CRUDEOIL") == (569900, "MCX_COMM")
    assert dc._underlying("NIFTY") == (13, "IDX_I")
    assert "CRUDEOIL" in dc.SUPPORTED and "NATURALGAS" in dc.SUPPORTED


def test_mcx_underlying_unresolvable_is_none_never_guessed(monkeypatch):
    from app.connectors import dhan_instruments as di
    monkeypatch.setattr(di, "resolve_front_month_future", lambda sym, **k: None)
    assert dc._underlying("NATURALGAS") is None
    monkeypatch.setattr(dc, "_headers", lambda: {"x": "y"})
    assert dc.fetch_raw("NATURALGAS", "23OCT2026") is None


@pytest.fixture
def expiry_http(monkeypatch):
    from app.connectors import dhan_instruments as di
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 30, 20, tzinfo=timezone.utc).astimezone(tz)  # Oct 1 IST
    monkeypatch.setattr(dc, "datetime", Clock)
    monkeypatch.setattr(dc, "_headers", lambda: {"test": "offline"})
    monkeypatch.setattr(di, "resolve_front_month_future",
                        lambda sym, **kw: {"security_id": "569900", "expiry": "2026-10-19"})
    class Response:
        status_code = 200
        def __init__(self, payload): self.payload = payload
        def json(self): return self.payload
    class Session:
        dates = []
        def __init__(self): self.calls = []
        def post(self, url, json, **kw):
            self.calls.append((url, json))
            return Response({"status": "success", "data": self.dates}
                            if url.endswith("/expirylist") else _REAL_SHAPED)
    return Session()


@pytest.mark.parametrize("symbol,scrip,segment,dates", [
    ("NIFTY", 13, "IDX_I", ["2026-10-06", "2026-10-13"]),
    ("BANKNIFTY", 25, "IDX_I", ["2026-10-27", "2026-11-24"]),
    ("SENSEX", 51, "IDX_I", ["2026-10-01", "2026-10-08"]),
    ("CRUDEOIL", 569900, "MCX_COMM", ["2026-10-15", "2026-11-17"]),
])
@pytest.mark.parametrize("expiry_request", ["AUTO", "NEXT", "EXPLICIT", "ISO"])
def test_fetch_resolves_listed_expiry_per_underlying(expiry_http, symbol, scrip, segment, dates, expiry_request):
    expiry_http.dates = [dates[1], "2026-09-30", dates[0], dates[0]]  # unsorted, expired, duplicate
    wanted = dates[0] if expiry_request == "AUTO" else dates[1]
    expiry = dc._to_canonical_expiry(wanted) if expiry_request == "EXPLICIT" else wanted if expiry_request == "ISO" else expiry_request
    chain = dc.fetch(symbol, expiry, session=expiry_http)
    assert chain is not None and chain.expiry == dc._to_canonical_expiry(wanted)
    assert expiry_http.calls[-1][1] == {"UnderlyingScrip": scrip, "UnderlyingSeg": segment, "Expiry": wanted}
    assert len(expiry_http.calls) == (2 if expiry_request in ("AUTO", "NEXT") else 1)


@pytest.mark.parametrize("dates,expiry_request", [([], "AUTO"), (["2026-09-30"], "AUTO"),
                                         (["2026-10-06"], "NEXT"), (["bad-date"], "AUTO")])
def test_missing_listed_expiry_never_substitutes_another_contract(expiry_http, dates, expiry_request):
    expiry_http.dates = dates
    assert dc.fetch("NIFTY", expiry_request, session=expiry_http) is None
    assert len(expiry_http.calls) == 1 and expiry_http.calls[0][0].endswith("/expirylist")


@pytest.mark.parametrize("raw,decimal", [(0.25, 0.0025), (1.0, 0.01), (1.5, 0.015),
                                       (12.25, 0.1225), ("1.0", 0.01), (" 9.789193798280868 ", 0.09789193798280868)])
def test_iv_is_always_a_percentage_not_a_magnitude_guess(raw, decimal):
    # Dhan v2 option-chain documentation uses 9.789193798280868 in its
    # response example. Our internal schema consistently uses fractions.
    from copy import deepcopy
    body = deepcopy(_REAL_SHAPED)
    body["data"]["oc"]["22750.000000"]["ce"]["implied_volatility"] = raw
    assert dc.parse(body, "NIFTY", "06OCT2026").leg(22750, "CE").iv == pytest.approx(decimal)


@pytest.mark.parametrize("raw", [None, "", "bad", {}, [], True, False, float("nan"),
                                float("inf"), float("-inf"), "NaN", "Infinity", -1.0, 0.0])
def test_invalid_iv_is_unavailable(raw):
    assert dc._iv(raw) is None
