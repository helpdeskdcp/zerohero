"""Fail-closed contract and snapshot regression tests for NSE/MCX."""
import time

from conftest import candles


def test_canonical_instrument_resolver_never_aliases_banknifty_to_nifty():
    from app import instruments
    assert instruments.canonical("NIFTY") == "NIFTY"
    assert instruments.canonical("BANKNIFTY") == "BANKNIFTY"
    assert instruments.canonical("FINNIFTY") == "FINNIFTY"
    for name in ("NATGASMINI", "CRUDEOILMINI", "GOLD", "SILVER"):
        assert instruments.canonical(name) == name


def test_mismatched_underlying_is_rejected_but_canonical_symbol_wins(fresh_db):
    from app.orchestrator import run_pipeline
    result = run_pipeline({"market": "NSE", "symbol": "BANKNIFTY", "underlying": "NIFTY",
                           "instrument": "OPTION", "candles": candles([100] * 60),
                           "account": {"capital": 100000}})
    contract = result["contract"]
    assert contract["symbol"] == contract["underlying"] == "BANKNIFTY"
    assert contract["final_decision"] == "NO_TRADE"
    assert "UNDERLYING_VALID failed" in contract["reason"]


def test_fresh_snapshot_is_ok_and_has_consistent_metadata(fresh_db):
    from app.orchestrator import run_pipeline
    result = run_pipeline({"market": "NSE", "symbol": "NIFTY", "candles": candles([100] * 60),
                           "instrument": "INDEX", "account": {"capital": 100000}})
    c = result["contract"]
    assert c["data_status"] == "OK"
    assert c["snapshot_id"].startswith("NSE-NIFTY-")
    assert c["data_timestamp"] and c["server_timestamp"]
    assert c["data_age_seconds"] >= 0


def test_stale_snapshot_cannot_trade(fresh_db):
    from app.orchestrator import run_pipeline
    old = candles([100] * 60, start=int(time.time()) - 6000)
    result = run_pipeline({"market": "NSE", "symbol": "NIFTY", "candles": old,
                           "instrument": "INDEX", "account": {"capital": 100000}})
    c = result["contract"]
    assert c["data_status"] == "STALE" and c["final_decision"] == "NO_TRADE"
    assert "data stale" in c["reason"]


def test_option_contract_fields_and_missing_oi_fail_closed(fresh_db):
    from app.orchestrator import run_pipeline
    result = run_pipeline({"market": "NSE", "symbol": "NIFTY", "instrument": "OPTION",
                           "candles": candles([100] * 60), "expiry": "",
                           "account": {"capital": 100000}})
    c = result["contract"]
    assert c["final_decision"] == "NO_TRADE"
    assert "options trade requires OI decision" in c["reason"]
    assert "expiry missing" in c["reason"]
    assert "strike missing" in c["reason"]
    assert "option_type missing" in c["reason"]


def test_risk_engine_rejects_missing_levels_and_low_rr():
    from app.engines.risk_engine import run_risk_engine
    base = {"account": {"capital": 100000, "risk_pct": 1}, "instrument": {"lot_size": 1}}
    assert run_risk_engine({**base, "signal": {"direction": "BUY", "entry_ref": None,
        "stop_loss": 90}})["risk_status"] == "REJECTED"
    assert run_risk_engine({**base, "signal": {"direction": "BUY", "entry_ref": 100,
        "stop_loss": 99, "target_1": 100.5}})["risk_status"] == "REJECTED"


def test_read_only_quote_adapter_normalizes_oi_without_orders(monkeypatch):
    from app.connectors import angelone
    monkeypatch.setattr(angelone, "fetch_market_quote", lambda *_a, **_k: {
        "status": "OK", "ltp": 12.5, "opnInterest": 1000,
        "changeinOpenInterest": 50, "tradeVolume": 900,
    })
    q = angelone.fetch_nse_option_chain("NIFTY", [{"symboltoken": "T",
        "exchange": "NFO", "strike": 22000, "expiry": "2026-09-03", "option_type": "CE"}])
    assert q["data_status"] == "OK" and q["rows"][0]["oi"] == 1000


def test_dynamic_expiry_and_atm_resolver_uses_master(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app import instruments
    # relative future expiries so AUTO does not roll past a hard-coded past date
    e1 = (datetime.now(timezone.utc) + timedelta(days=6)).strftime("%d%b%Y").upper()
    e2 = (datetime.now(timezone.utc) + timedelta(days=13)).strftime("%d%b%Y").upper()
    monkeypatch.setattr(instruments, "master_rows", lambda **_k: [
        {"exch_seg": "NFO", "symbol": f"NIFTY{e1}CE22000", "token": "1", "name": "NIFTY", "expiry": e1, "strike": "2200000"},
        {"exch_seg": "NFO", "symbol": f"NIFTY{e2}CE22000", "token": "2", "name": "NIFTY", "expiry": e2, "strike": "2200000"},
    ])
    c = instruments.resolve_nse_option("NIFTY", "AUTO", "ATM", "CE", spot=22000)
    assert c["status"] == "OK" and c["expiry"] == e1 and c["symboltoken"] == "1"
    assert c["next_expiry"] == e2
