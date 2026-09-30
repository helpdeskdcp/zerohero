"""app.histcap.dhan_capture -- Dhan chain -> canonical capture rows,
PARALLEL to AngelOne's own normalize.norm_quote/norm_greeks. Offline:
dhan_chain.fetch_raw is monkeypatched (the one real network call), the
real dhan_chain.parse() runs on it -- no network, no live app import."""
from app.histcap import dhan_capture as DC

_RAW = {
    "status": "success",
    "data": {
        "last_price": 22763.9,
        "oc": {
            "22750.000000": {
                "ce": {"last_price": 166.95, "oi": 5670795, "previous_oi": 758355,
                      "volume": 88082215, "top_bid_price": 166.95, "top_ask_price": 167.35,
                      "top_bid_quantity": 390, "top_ask_quantity": 780,
                      "implied_volatility": 11.5, "security_id": 49081,
                      "greeks": {"delta": 0.566, "gamma": 0.0011, "theta": -14.17, "vega": 11.83}},
                "pe": {"last_price": 148.3, "oi": 3200000, "previous_oi": 3500000,
                      "volume": 50000000, "top_bid_price": 148.1, "top_ask_price": 148.5,
                      "top_bid_quantity": 200, "top_ask_quantity": 300,
                      "implied_volatility": None},   # no greeks/security_id -- real, disclosed gap
            },
        },
    },
}


def test_capture_dhan_chain_builds_quote_and_greek_rows(monkeypatch):
    monkeypatch.setattr(DC._dhan, "fetch_raw", lambda u, e: _RAW)
    out = DC.capture_dhan_chain("NIFTY", "06OCT2026")
    assert out is not None
    assert out["spot"] == 22763.9
    assert out["raw"] is _RAW                   # archived verbatim, the one real response
    assert len(out["quote_rows"]) == 2          # CE + PE
    assert len(out["greek_rows"]) == 1          # only CE has greeks in this fixture

    ce_q = next(r for r in out["quote_rows"] if r["option_type"] == "CE")
    assert ce_q["symbol"] == "NIFTY" and ce_q["kind"] == "OPTION" and ce_q["source"] == "DHAN_CHAIN"
    assert ce_q["strike"] == 22750.0 and ce_q["ltp"] == 166.95
    assert ce_q["bid"] == 166.95 and ce_q["ask"] == 167.35
    assert ce_q["oi"] == 5670795 and ce_q["oi_change"] == 5670795 - 758355
    assert ce_q["token"] == "49081"              # real Dhan security_id used as token

    pe_q = next(r for r in out["quote_rows"] if r["option_type"] == "PE")
    assert pe_q["token"] == "DHAN-NIFTY-06OCT2026-22750.0-PE"   # synthetic fallback, no security_id given

    ce_g = out["greek_rows"][0]
    assert ce_g["source"] == "DHAN_OPTION_GREEK" and ce_g["delta"] == 0.566
    # raw 11.5 (%) -> _iv() normalises to decimal 0.115 -> iv_pct round-trips to 11.5
    assert ce_g["iv"] == 0.115 and ce_g["iv_pct"] == 11.5


def test_capture_dhan_chain_returns_none_when_fetch_raw_returns_none(monkeypatch):
    monkeypatch.setattr(DC._dhan, "fetch_raw", lambda u, e: None)
    assert DC.capture_dhan_chain("NIFTY", "06OCT2026") is None


def test_capture_dhan_chain_returns_none_when_fetch_raw_raises(monkeypatch):
    def _boom(u, e):
        raise RuntimeError("network down")
    monkeypatch.setattr(DC._dhan, "fetch_raw", _boom)
    assert DC.capture_dhan_chain("NIFTY", "06OCT2026") is None


def test_capture_dhan_chain_returns_none_for_empty_chain(monkeypatch):
    empty = {"status": "success", "data": {"last_price": 22763.9, "oc": {}}}
    monkeypatch.setattr(DC._dhan, "fetch_raw", lambda u, e: empty)
    assert DC.capture_dhan_chain("NIFTY", "06OCT2026") is None


def test_capture_dhan_chain_returns_none_for_malformed_response(monkeypatch):
    monkeypatch.setattr(DC._dhan, "fetch_raw", lambda u, e: {"status": "success"})   # missing "data" entirely
    assert DC.capture_dhan_chain("NIFTY", "06OCT2026") is None


def test_dhan_tokens_never_collide_with_angelone_numeric_tokens(monkeypatch):
    # AngelOne tokens are always plain digit strings (e.g. "49081"); a
    # synthetic Dhan token must never accidentally collide with one.
    monkeypatch.setattr(DC._dhan, "fetch_raw", lambda u, e: _RAW)
    out = DC.capture_dhan_chain("NIFTY", "06OCT2026")
    pe_token = next(r for r in out["quote_rows"] if r["option_type"] == "PE")["token"]
    assert not pe_token.isdigit()


def test_capture_dhan_chain_raw_archive_never_contains_credentials(monkeypatch):
    # The archived raw response is exactly Dhan's market-data body -- it
    # structurally cannot contain the request's auth headers (see
    # dhan_chain.fetch_raw()'s own docstring/test for the guarantee this
    # relies on). This asserts the property holds end-to-end through
    # capture_dhan_chain()'s own return value too.
    monkeypatch.setattr(DC._dhan, "fetch_raw", lambda u, e: _RAW)
    out = DC.capture_dhan_chain("NIFTY", "06OCT2026")
    dumped = str(out["raw"])
    for secret_marker in ("access-token", "access_token", "client-id", "client_id", "mpin", "otp", "password"):
        assert secret_marker not in dumped.lower()
