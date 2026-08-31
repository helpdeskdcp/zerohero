"""Focused read-only selector tests; no network or broker order API."""
from app import market_data


class FakeSDK:
    def __init__(self):
        self.rows = [
            {"exch_seg": "NSE", "instrumenttype": "AMXIDX", "name": "NIFTY", "symbol": "NIFTY", "token": "n"},
            {"exch_seg": "NSE", "instrumenttype": "AMXIDX", "name": "BANKNIFTY", "symbol": "BANKNIFTY", "token": "b"},
            {"exch_seg": "MCX", "instrumenttype": "FUTCOM", "name": "CRUDEOILMINI", "symbol": "CRUDEOILMINI29SEP2026", "token": "m", "expiry": "29SEP2026"},
        ]
    def _date(self, value):
        from datetime import datetime
        return datetime.strptime(value, "%d%b%Y").date()
    def load_instrument_master(self): return self.rows
    def resolve_index(self, symbol): return {"status": "OK", "exchange": "NSE", "token": "n", "symbol": symbol, "underlying": symbol}
    def resolve_equity(self, symbol): return {"status": "INSTRUMENT_MASTER_CONTRACT_NOT_FOUND"}
    def resolve_option_contract(self, symbol, expiry, strike, typ, spot):
        return {"status": "OK", "exchange": "NFO", "token": typ + "1", "symbol": symbol + typ,
                "underlying": symbol, "expiry": "01SEP2026", "strike": 22500, "available_expiries": ["01SEP2026"]}
    def resolve_future_contract(self, symbol, expiry):
        return {"status": "OK", "exchange": "MCX", "token": "m", "symbol": "CRUDEOILMINI29SEP2026", "underlying": symbol, "expiry": "29SEP2026"}
    def get_quote(self, exchange, token):
        return {"status": "OK", "ltp": 0 if token == "m" else 22495, "opnInterest": 0,
                "tradeVolume": 12, "exchangeTimestamp": "2026-08-29T10:00:00+05:30"}
    def get_option_chain(self, symbol, expiry, window):
        return {"status": "OK", "timestamp": "2026-08-29T10:00:00+05:30", "rows": [
            {"strike": 22500, "ce": {"token": "CE1", "ltp": 0, "oi": 0, "oi_change": None, "volume": 1},
             "pe": {"token": "PE1", "ltp": 2, "oi": 3, "oi_change": 0, "volume": 4}}]}


def test_dynamic_nse_and_mcx_symbols_are_from_master():
    sdk = FakeSDK()
    assert [x["name"] for x in market_data.available_symbols(sdk, "NSE")] == ["BANKNIFTY", "NIFTY"]
    assert [x["name"] for x in market_data.available_symbols(sdk, "MCX")] == ["CRUDEOILMINI"]


def test_nifty_option_snapshot_uses_live_spot_and_master_atm():
    result = market_data.selection_snapshot(FakeSDK(), "NSE", "NIFTY")
    assert result["status"] == "OK"
    assert result["spot"] == 22495.0 and result["atm"] == 22500
    assert result["contracts"]["CE"]["token"] == "CE1"
    assert result["chain"][0]["ce_ltp"] == 0.0  # real zero is preserved, not fabricated
    assert result["chain"][0]["ce_oi_change"] is None


def test_banknifty_selection_and_mcx_contract_resolution():
    nse = market_data.selection_snapshot(FakeSDK(), "NSE", "BANKNIFTY")
    mcx = market_data.selection_snapshot(FakeSDK(), "MCX", "CRUDEOILMINI")
    assert nse["underlying"] == "BANKNIFTY" and nse["expiry"] == "01SEP2026"
    assert mcx["contract"]["token"] == "m" and mcx["expiry"] == "29SEP2026"
    assert mcx["quote"]["ltp"] == 0.0 and mcx["quote"]["oi"] == 0.0


class EquityFakeSDK(FakeSDK):
    """An NSE F&O stock: not an index, but has listed NFO options."""
    def resolve_index(self, symbol):
        return {"status": "INSTRUMENT_MASTER_CONTRACT_NOT_FOUND"}
    def resolve_equity(self, symbol):
        return {"status": "OK", "exchange": "NSE", "symbol": symbol + "-EQ",
                "token": "eq1", "underlying": symbol, "instrument_type": "EQ"}
    def search_instruments(self, *, symbol=None, exchange=None, instrumenttype=None):
        if str(exchange or "").upper() == "NFO" and symbol == "RELIANCE":
            return [{"exch_seg": "NFO", "instrumenttype": "OPTSTK", "name": "RELIANCE"}]
        return []


def test_equity_fno_stock_routes_to_option_chain_not_spot():
    r = market_data.selection_snapshot(EquityFakeSDK(), "NSE", "RELIANCE")
    assert r["instrument"] == "OPTION" and r["status"] == "OK"
    assert r["underlying"] == "RELIANCE" and r["atm"] == 22500
    assert r["contracts"]["CE"]["token"] == "CE1"


def test_cash_only_stock_still_falls_back_to_spot():
    # no NFO rows for this name -> SPOT view, not a spurious DATA_UNAVAILABLE
    r = market_data.selection_snapshot(EquityFakeSDK(), "NSE", "SOMECASHONLY")
    assert r["instrument"] == "SPOT" and r["underlying"] == "SOMECASHONLY"


def test_explicit_spot_request_on_equity_is_honoured():
    r = market_data.selection_snapshot(EquityFakeSDK(), "NSE", "RELIANCE", instrument="SPOT")
    assert r["instrument"] == "SPOT"


def test_missing_quote_is_controlled_data_unavailable():
    sdk = FakeSDK()
    sdk.get_quote = lambda *_: {"status": "DATA_UNAVAILABLE"}
    result = market_data.selection_snapshot(sdk, "NSE", "NIFTY")
    assert result["status"] == "DATA_UNAVAILABLE"


def test_run_pipeline_boundary_converts_market_exception_to_data_unavailable(monkeypatch):
    import asyncio
    from app import main
    monkeypatch.setattr(main, "run_pipeline", lambda _payload: (_ for _ in ()).throw(RuntimeError("broker down")))
    response = asyncio.run(main.api_run_pipeline(main.SignalRequest(market="NSE", symbol="NIFTY")))
    assert response["error"] == "DATA_UNAVAILABLE"
    assert response["contract"]["data_status"] == "DATA_UNAVAILABLE"


def test_run_pipeline_broker_path_does_not_shadow_datetime(monkeypatch, fresh_db):
    from app import orchestrator
    monkeypatch.setattr(orchestrator.angelone, "fetch_candles", lambda **_kwargs: {
        "market": "NSE", "symbol": "NIFTY", "instrument": "INDEX", "data_status": "DATA_UNAVAILABLE",
        "candles": [], "fetched_at": "2026-08-29T10:00:00+00:00"})
    result = orchestrator.run_pipeline({"market": "NSE", "symbol": "NIFTY", "instrument": "INDEX"})
    assert result["contract"]["data_status"] == "DATA_UNAVAILABLE"


def test_copy_snapshot_ui_uses_clipboard_and_na_display():
    from pathlib import Path
    js = (Path(__file__).parents[2] / "frontend" / "static" / "js" / "app.js").read_text()
    html = (Path(__file__).parents[2] / "frontend" / "index.html").read_text()
    assert 'id="copyMarketSnapshot"' in html
    assert "navigator.clipboard.writeText" in js
    assert "Data Timestamp:" in js and ' ? "N/A" : ' in js
