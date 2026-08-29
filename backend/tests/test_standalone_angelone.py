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
