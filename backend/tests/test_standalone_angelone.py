import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[2]))
from broker.angelone.client import AngelOneClient


def test_standalone_resolver_uses_valid_master_and_expiry(monkeypatch, tmp_path):
    c = AngelOneClient(cache_path=str(tmp_path / "m.json"))
    c._master = [
        {"exch_seg":"NFO","name":"BANKNIFTY","symbol":"BANKNIFTY01SEP202650000CE","token":"1","instrumenttype":"OPTIDX","expiry":"01SEP2026","strike":"5000000","lotsize":"15"},
        {"exch_seg":"NFO","name":"BANKNIFTY","symbol":"BANKNIFTY08SEP202650000CE","token":"2","instrumenttype":"OPTIDX","expiry":"08SEP2026","strike":"5000000","lotsize":"15"},
    ]
    r = c.resolve_option_contract("BANKNIFTY", "AUTO", "ATM", "CE", spot=50000)
    assert r["status"] == "OK" and r["underlying"] == "BANKNIFTY" and r["token"] == "1"
    assert r["available_expiries"] == ["01SEP2026", "08SEP2026"]
    assert r["exchange"] == "NFO"


def test_resolver_handles_mcx_options_on_futures(monkeypatch, tmp_path):
    c = AngelOneClient(cache_path=str(tmp_path / "m.json"))
    c._master = [
        {"exch_seg":"MCX","name":"NATURALGAS","symbol":"NATURALGAS23SEP26255CE","token":"11",
         "instrumenttype":"OPTFUT","expiry":"23SEP2026","strike":"25500.000000","lotsize":"1250"},
        {"exch_seg":"MCX","name":"NATURALGAS","symbol":"NATURALGAS23SEP26250CE","token":"12",
         "instrumenttype":"OPTFUT","expiry":"23SEP2026","strike":"25000.000000","lotsize":"1250"},
        {"exch_seg":"MCX","name":"NATURALGAS","symbol":"NATURALGAS23SEP26250PE","token":"13",
         "instrumenttype":"OPTFUT","expiry":"23SEP2026","strike":"25000.000000","lotsize":"1250"},
        {"exch_seg":"MCX","name":"NATURALGAS","symbol":"NATURALGAS23SEP26FUT","token":"90",
         "instrumenttype":"FUTCOM","expiry":"23SEP2026","strike":"0","lotsize":"1250"},
    ]
    # ATM around a 250.5 future price -> the 250 strike
    r = c.resolve_option_contract("NATURALGAS", "AUTO", "ATM", "CE", spot=250.5)
    assert r["status"] == "OK" and r["exchange"] == "MCX" and r["token"] == "12"
    assert r["strike"] == 250.0 and r["option_type"] == "CE"


def test_auto_expiry_is_chronological_not_lexical(tmp_path):
    # MCX expiries span months; "20NOV" sorts before "23SEP" lexically. AUTO
    # must still pick the NEAREST by date (23SEP), not the alphabetical first.
    c = AngelOneClient(cache_path=str(tmp_path / "m.json"))
    c._master = [
        {"exch_seg":"MCX","name":"NATURALGAS","symbol":"NATURALGAS23SEP26250CE","token":"1",
         "instrumenttype":"OPTFUT","expiry":"23SEP2026","strike":"25000","lotsize":"1250"},
        {"exch_seg":"MCX","name":"NATURALGAS","symbol":"NATURALGAS23OCT26250CE","token":"2",
         "instrumenttype":"OPTFUT","expiry":"23OCT2026","strike":"25000","lotsize":"1250"},
        {"exch_seg":"MCX","name":"NATURALGAS","symbol":"NATURALGAS20NOV26250CE","token":"3",
         "instrumenttype":"OPTFUT","expiry":"20NOV2026","strike":"25000","lotsize":"1250"},
    ]
    r = c.resolve_option_contract("NATURALGAS", "AUTO", "ATM", "CE", spot=250.0)
    assert r["expiry"] == "23SEP2026" and r["token"] == "1"
    assert r["available_expiries"] == ["23SEP2026", "23OCT2026", "20NOV2026"]
