"""
AngelOne adapter upgrade — Option-Greek acquisition + per-strike merge.

Data-layer only. No network: `requests.post` is monkeypatched. No trading /
signal logic is exercised. Covers step-10 of the adapter-upgrade brief:
success / empty / AB9019 / malformed / missing-field / strike-match / CE-PE /
expiry / field-preservation / cache-dedup / API-failure isolation.
"""
import sys
from datetime import datetime, timedelta, timezone as _tz
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parents[2]))

from broker.angelone import client as cli                     # noqa: E402
from broker.angelone.client import AngelOneClient             # noqa: E402
from broker.angelone import greeks as gk                      # noqa: E402
from broker.angelone.capability import (                      # noqa: E402
    adapter_capability_report, format_capability_report,
)

# a live (non-expired) expiry so resolve_option_contract keeps the contracts
_FUT_EXP = (datetime.now(_tz.utc) + timedelta(days=25)).strftime("%d%b%Y").upper()

# exact row shape from SmartAPI forum topic 4254 (values are strings; IV is %)
_CE = {"name": "TCS", "expiry": _FUT_EXP, "strikePrice": "3900.000000", "optionType": "CE",
       "delta": "0.492400", "gamma": "0.002800", "theta": "-4.091800", "vega": "2.296700",
       "impliedVolatility": "16.330000", "tradeVolume": "24048.00"}
_PE = {"name": "TCS", "expiry": _FUT_EXP, "strikePrice": "3900.000000", "optionType": "PE",
       "delta": "-0.507600", "gamma": "0.002800", "theta": "-3.900000", "vega": "2.290000",
       "impliedVolatility": "16.900000", "tradeVolume": "111.00"}


class _Resp:
    def __init__(self, payload, code=200, content=b"{}", json_exc=None):
        self._p, self.status_code, self.content, self._exc = payload, code, content, json_exc

    def json(self):
        if self._exc:
            raise self._exc
        return self._p


def _client(monkeypatch):
    monkeypatch.setenv("ANGEL_API_KEY", "test-key")
    monkeypatch.setenv("ANGEL_GREEK_TTL_SEC", "15")
    c = AngelOneClient(cache_path="/tmp/gk_adapter_test.json")
    monkeypatch.setattr(c, "authenticate", lambda: True)
    c.jwt = "test-jwt"
    return c


def _post_returning(payload, **kw):
    calls = []

    def _post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "body": json})
        return _Resp(payload, **kw)

    _post.calls = calls
    return _post


# ------------------------------------------------------------------ raw fetch
def test_greek_success_normalizes_every_field(monkeypatch):
    c = _client(monkeypatch)
    post = _post_returning({"status": True, "message": "SUCCESS", "errorcode": "", "data": [_CE, _PE]})
    monkeypatch.setattr(cli.requests, "post", post)

    res = c.get_option_greeks("TCS", "25JAN2024")
    assert res["status"] == "OK" and res["cache"] == "MISS"
    assert res["source"] == "ANGELONE_OPTION_GREEK"
    assert res["endpoint"].endswith("/marketData/v1/optionGreek")   # correct path, not market/v1
    assert post.calls[0]["body"] == {"name": "TCS", "expirydate": "25JAN2024"}
    ce = next(r for r in res["rows"] if r["option_type"] == "CE")
    assert ce["strike"] == 3900.0
    assert ce["delta"] == 0.4924 and ce["gamma"] == 0.0028
    assert ce["theta"] == -4.0918 and ce["vega"] == 2.2967
    assert ce["iv_pct"] == 16.33 and abs(ce["iv"] - 0.1633) < 1e-9   # % kept + decimal fraction
    assert ce["status"] == "OK" and ce["trade_volume"] == 24048.0


def test_greek_empty_data_is_no_data(monkeypatch):
    c = _client(monkeypatch)
    monkeypatch.setattr(cli.requests, "post", _post_returning({"status": True, "data": []}))
    res = c.get_option_greeks("TCS", "25JAN2024")
    assert res["status"] == "NO_DATA" and res["rows"] == []


def test_greek_ab9019_is_no_data_with_errorcode(monkeypatch):
    c = _client(monkeypatch)
    monkeypatch.setattr(cli.requests, "post", _post_returning(
        {"status": False, "errorcode": "AB9019", "message": "No Data Available", "data": None}))
    res = c.get_option_greeks("TCS", "25JAN2024")
    assert res["status"] == "NO_DATA" and res["errorcode"] == "AB9019"
    assert res["rows"] == []


def test_greek_malformed_json_isolated(monkeypatch):
    c = _client(monkeypatch)
    monkeypatch.setattr(cli.requests, "post",
                        _post_returning(None, json_exc=ValueError("not json"), content=b"<html>"))
    res = c.get_option_greeks("TCS", "25JAN2024")
    assert res["status"] == "MALFORMED" and res["errorcode"] == "BAD_JSON" and res["rows"] == []


def test_greek_timeout_isolated(monkeypatch):
    c = _client(monkeypatch)

    def _boom(*a, **k):
        raise requests.Timeout("slow")

    monkeypatch.setattr(cli.requests, "post", _boom)
    res = c.get_option_greeks("TCS", "25JAN2024")
    assert res["status"] == "TIMEOUT" and res["rows"] == []


def test_greek_rate_limited(monkeypatch):
    c = _client(monkeypatch)
    monkeypatch.setattr(cli.requests, "post", _post_returning({}, code=429))
    res = c.get_option_greeks("TCS", "25JAN2024")
    assert res["status"] == "RATE_LIMITED" and res["http_status"] == 429


def test_greek_api_error_isolated(monkeypatch):
    c = _client(monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(cli.requests, "post", _boom)
    res = c.get_option_greeks("TCS", "25JAN2024")
    assert res["status"] == "API_ERROR" and "connection reset" in res["message"]


def test_greek_auth_failure_isolated(monkeypatch):
    c = _client(monkeypatch)
    monkeypatch.setattr(c, "authenticate", lambda: False)
    c.last_auth = {"status": "CONFIG_REQUIRED"}
    res = c.get_option_greeks("TCS", "25JAN2024")
    assert res["status"] == "AUTH_FAILED" and res["rows"] == []


# ------------------------------------------------------------------ normalize
def test_missing_individual_greek_field_is_none_not_zero():
    row = dict(_CE)
    row.pop("delta")
    nr = gk.normalize_greek_row(row)
    assert nr["delta"] is None and nr["gamma"] == 0.0028 and nr["status"] == "OK"


def test_row_with_no_greeks_is_malformed():
    nr = gk.normalize_greek_row({"strikePrice": "3900.0", "optionType": "CE"})
    assert nr["status"] == "MALFORMED"


def test_iv_zero_string_is_none_not_zero():
    nr = gk.normalize_greek_row({**_CE, "impliedVolatility": ""})
    assert nr["iv"] is None and nr["iv_pct"] is None and nr["status"] == "OK"


# ------------------------------------------------------------------ matching
def test_strike_matching_tolerance():
    idx = gk.index_greek_rows([gk.normalize_greek_row(_CE)])
    assert gk.match_greek(idx, 3900.0, "CE")["delta"] == 0.4924       # exact
    assert gk.match_greek(idx, 3900.0004, "CE") is not None           # within tol
    assert gk.match_greek(idx, 3925.0, "CE") is None                  # beyond tol
    assert gk.match_greek(idx, None, "CE") is None


def test_ce_pe_mapping_is_distinct():
    idx = gk.index_greek_rows([gk.normalize_greek_row(_CE), gk.normalize_greek_row(_PE)])
    ce, pe = gk.match_greek(idx, 3900, "CE"), gk.match_greek(idx, 3900, "PE")
    assert ce["delta"] > 0 > pe["delta"] and ce is not pe


# ------------------------------------------------------------------ merge / preservation
def test_merge_never_overwrites_quote_fields():
    leg = {"token": "T1", "ltp": 100.0, "oi": 5000, "oi_change": 12, "volume": 999, "bid": 99.5, "ask": 100.5}
    grow = gk.normalize_greek_row(_CE)
    out = gk.merge_leg_greeks(leg, grow)
    assert (out["ltp"], out["oi"], out["oi_change"], out["volume"], out["token"]) == (100.0, 5000, 12, 999, "T1")
    assert out["bid"] == 99.5 and out["ask"] == 100.5
    assert out["delta"] == 0.4924 and out["iv"] is not None
    ds = out["data_source"]
    assert ds["ltp"] == "ANGELONE_QUOTE" and ds["oi"] == "ANGELONE_QUOTE" and ds["volume"] == "ANGELONE_QUOTE"
    assert ds["delta"] == "ANGELONE_OPTION_GREEK" and ds["iv"] == "ANGELONE_OPTION_GREEK"


def test_merge_keeps_an_existing_greek_value_if_broker_quote_had_one():
    leg = {"token": "T1", "ltp": 100.0, "delta": 0.9}          # hypothetical quote-sourced delta
    out = gk.merge_leg_greeks(leg, gk.normalize_greek_row(_CE))
    assert out["delta"] == 0.9 and out["data_source"]["delta"] == "ANGELONE_QUOTE"


def test_merge_with_no_greek_row_leaves_none_and_marks_unavailable():
    out = gk.merge_leg_greeks({"token": "T1", "ltp": 100.0}, None)
    assert out["delta"] is None and out["greeks_source"] == "UNAVAILABLE"
    assert out["data_source"]["delta"] is None and out["data_source"]["ltp"] == "ANGELONE_QUOTE"


# ------------------------------------------------------------------ cache / dedup
def test_duplicate_call_hits_cache(monkeypatch):
    c = _client(monkeypatch)
    post = _post_returning({"status": True, "data": [_CE, _PE]})
    monkeypatch.setattr(cli.requests, "post", post)

    a = c.get_option_greeks("TCS", "25JAN2024")
    b = c.get_option_greeks("TCS", "25JAN2024")
    assert a["cache"] == "MISS" and b["cache"] == "HIT"
    assert len(post.calls) == 1
    c.get_option_greeks("INFY", "25JAN2024")          # different key -> new request
    assert len(post.calls) == 2


def test_error_is_negative_cached(monkeypatch):
    c = _client(monkeypatch)
    post = _post_returning({"status": False, "errorcode": "AB9019", "message": "No Data Available"})
    monkeypatch.setattr(cli.requests, "post", post)
    a = c.get_option_greeks("TCS", "25JAN2024")
    b = c.get_option_greeks("TCS", "25JAN2024")
    assert a["status"] == "NO_DATA" and b["cache"] == "HIT" and len(post.calls) == 1


def test_expiry_is_passed_through_verbatim(monkeypatch):
    c = _client(monkeypatch)
    post = _post_returning({"status": True, "data": [_CE]})
    monkeypatch.setattr(cli.requests, "post", post)
    c.get_option_greeks("nifty", "08feb2024")
    assert post.calls[0]["body"] == {"name": "NIFTY", "expirydate": "08FEB2024"}


# ------------------------------------------------------------------ full chain integration
# master strike is in paise (x100): "3900000" -> 39000.0 rupees
_MASTER = [
    {"exch_seg": "NSE", "name": "NIFTY", "symbol": "NIFTY", "token": "IDX", "instrumenttype": "AMXIDX"},
    {"exch_seg": "NFO", "name": "NIFTY", "symbol": f"NIFTY{_FUT_EXP}38950CE", "token": "C1",
     "instrumenttype": "OPTIDX", "expiry": _FUT_EXP, "strike": "3895000", "lotsize": "50"},
    {"exch_seg": "NFO", "name": "NIFTY", "symbol": f"NIFTY{_FUT_EXP}38950PE", "token": "P1",
     "instrumenttype": "OPTIDX", "expiry": _FUT_EXP, "strike": "3895000", "lotsize": "50"},
    {"exch_seg": "NFO", "name": "NIFTY", "symbol": f"NIFTY{_FUT_EXP}39000CE", "token": "C2",
     "instrumenttype": "OPTIDX", "expiry": _FUT_EXP, "strike": "3900000", "lotsize": "50"},
    {"exch_seg": "NFO", "name": "NIFTY", "symbol": f"NIFTY{_FUT_EXP}39000PE", "token": "P2",
     "instrumenttype": "OPTIDX", "expiry": _FUT_EXP, "strike": "3900000", "lotsize": "50"},
]
_ATM = 39000.0

_LEG_QUOTE = {"status": "OK", "ltp": 152.0, "opnInterest": 120000, "tradeVolume": 4400,
              "changeinOpenInterest": 3000, "open": 150, "high": 158, "low": 149, "close": 151,
              "netChange": 1.0, "percentChange": 0.66,
              "depth": {"buy": [{"price": 151.8, "quantity": 50, "orders": 2}],
                        "sell": [{"price": 152.2, "quantity": 75, "orders": 3}]},
              "exchangeTimestamp": "2024-01-24T10:00:00+05:30"}


def _quote_stub(exchange, token):
    if str(token) == "IDX":
        return {"status": "OK", "ltp": _ATM}                 # index spot
    return {**_LEG_QUOTE, "symboltoken": str(token)}          # option leg


def _chain_client(monkeypatch, greek_payload):
    c = _client(monkeypatch)
    c._master = list(_MASTER)
    monkeypatch.setattr(c, "get_quote", _quote_stub)
    if isinstance(greek_payload, Exception):
        def _boom(*a, **k):
            raise greek_payload
        monkeypatch.setattr(cli.requests, "post", _boom)
    else:
        monkeypatch.setattr(cli.requests, "post", _post_returning(greek_payload))
    return c


def test_option_chain_merges_greeks_per_strike_and_type(monkeypatch):
    g_ce = {**_CE, "expiry": _FUT_EXP, "strikePrice": "39000.000000"}
    g_pe = {**_PE, "expiry": _FUT_EXP, "strikePrice": "39000.000000"}
    c = _chain_client(monkeypatch, {"status": True, "data": [g_ce, g_pe]})

    chain = c.get_option_chain("NIFTY", "AUTO", window=2)
    assert chain["status"] == "OK" and chain["expiry"] == _FUT_EXP
    assert chain["greeks"]["status"] == "OK" and chain["greeks"]["matched"] >= 2

    row = next(r for r in chain["rows"] if r["strike"] == _ATM)
    ce, pe = row["ce"], row["pe"]
    # quote-sourced fields intact
    assert ce["ltp"] == 152.0 and ce["oi"] == 120000 and ce["volume"] == 4400
    assert ce["bid"] == 151.8 and ce["ask"] == 152.2 and ce["token"] == "C2"
    # greek-sourced fields merged, correct per type
    assert ce["delta"] == 0.4924 and pe["delta"] == -0.5076
    assert ce["iv_pct"] == 16.33 and abs(ce["iv"] - 0.1633) < 1e-9
    # provenance
    assert ce["data_source"]["ltp"] == "ANGELONE_QUOTE"
    assert ce["data_source"]["delta"] == "ANGELONE_OPTION_GREEK"
    assert ce["data_source"]["iv"] == "ANGELONE_OPTION_GREEK"


def test_option_chain_greek_failure_does_not_break_the_chain(monkeypatch):
    c = _chain_client(monkeypatch, RuntimeError("greek endpoint down"))
    chain = c.get_option_chain("NIFTY", "AUTO", window=2)
    assert chain["status"] == "OK" and len(chain["rows"]) >= 1
    assert chain["greeks"]["status"] in ("API_ERROR", "TIMEOUT", "NO_DATA", "MALFORMED")
    ce = next(r for r in chain["rows"] if r["strike"] == _ATM)["ce"]
    assert ce["ltp"] == 152.0 and ce["oi"] == 120000            # quote data still present
    assert ce["delta"] is None and ce["data_source"]["delta"] is None
    assert ce["greeks_source"] == "UNAVAILABLE"


def test_option_chain_no_greek_data_is_clean(monkeypatch):
    c = _chain_client(monkeypatch, {"status": False, "errorcode": "AB9019",
                                    "message": "No Data Available", "data": None})
    chain = c.get_option_chain("NIFTY", "AUTO", window=2)
    assert chain["status"] == "OK"
    assert chain["greeks"]["status"] == "NO_DATA" and chain["greeks"]["matched"] == 0
    ce = next(r for r in chain["rows"] if r["strike"] == _ATM)["ce"]
    assert ce["delta"] is None and ce["ltp"] == 152.0


def test_option_chain_one_greek_request_for_the_whole_chain(monkeypatch):
    c = _chain_client(monkeypatch, {"status": True, "data": [{**_CE, "strikePrice": "39000.000000"}]})
    c.get_option_chain("NIFTY", "AUTO", window=2)
    assert len(cli.requests.post.calls) == 1                     # NOT one-per-strike


# ------------------------------------------------------------------ capability report
def test_capability_report_shape_and_no_false_claims():
    rows = adapter_capability_report()
    assert rows and all(set(r) >= {"field", "availability", "endpoint", "wired", "source", "status", "note"} for r in rows)
    g = {r["field"]: r for r in rows}
    assert g["delta"]["endpoint"].endswith("marketData/v1/optionGreek") and g["delta"]["wired"] is True
    assert g["delta"]["status"] == "DOC"                         # not "available" until a live probe
    assert g["rho"]["availability"] == "NOT_PROVIDED"
    assert g["synthetic greeks (no-IV)"]["availability"] == "DERIVED_ELSEWHERE"
    txt = format_capability_report(rows)
    assert "FIELD" in txt and "marketData/v1/optionGreek" in txt


def test_capability_probe_reports_exact_error_when_unauth(monkeypatch):
    c = _client(monkeypatch)
    monkeypatch.setattr(c, "authenticate", lambda: False)
    c.last_auth = {"status": "CONFIG_REQUIRED"}
    monkeypatch.setattr(c, "resolve_index", lambda s: {"status": "OK", "token": "IDX"})
    monkeypatch.setattr(c, "get_quote", lambda *a: {"status": "AUTH_FAILED"})
    monkeypatch.setattr(c, "resolve_option_contract",
                        lambda *a, **k: {"status": "OK", "expiry": "25JAN2024"})
    rows = adapter_capability_report(c, probe=True)
    g = {r["field"]: r for r in rows}
    assert g["delta"]["status"].startswith("AUTH_FAILED")
    assert g["ltp"]["status"] == "AUTH_FAILED"
