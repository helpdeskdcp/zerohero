"""app.optionchain.sources.dhan_chain -- pure parse() tests on canned JSON
shaped exactly like the real Dhan v2 /optionchain response (verified live
2026-09-30: NIFTY 06OCT2026, 238 real strikes, 100% Greek coverage). No
network, no auth -- fetch()'s HTTP/auth path is exercised only by the
live verification, not the automated suite."""
import pytest

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
                      "implied_volatility": 12.249246498755785,
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
    assert dc.fetch("CRUDEOIL", "06OCT2026") is None    # MCX not wired -- see module docstring


def test_fetch_returns_none_without_credentials(monkeypatch):
    monkeypatch.setattr(dc, "_headers", lambda: None)
    assert dc.fetch("NIFTY", "06OCT2026") is None


def test_to_iso_date_accepts_canonical_and_iso():
    assert dc._to_iso_date("06OCT2026") == "2026-10-06"
    assert dc._to_iso_date("2026-10-06") == "2026-10-06"
    assert dc._to_iso_date("not-a-date") is None


def test_to_canonical_expiry_round_trips():
    assert dc._to_canonical_expiry("2026-10-06") == "06OCT2026"
