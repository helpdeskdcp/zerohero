"""AngelOne adapter: SENSEX / BANKEX (BSE index, options on BFO) resolution."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[2]))
from broker.angelone.client import AngelOneClient


class _FakeSDK(AngelOneClient):
    def __init__(self):
        super().__init__(cache_path="/tmp/bfo_test.json")
        self._q = {"99919000": {"ltp": 76150.0}}

    def search_instruments(self, *, symbol=None, exchange=None, instrumenttype=None):
        # SENSEX has no NSE AMXIDX row and no MCX OPTFUT row
        if exchange == "BFO" and symbol == "SENSEX":
            return [{"symbol": "SENSEX26O0176500CE", "token": "1", "strike": "7650000",
                     "expiry": "01OCT2026", "instrumenttype": "OPTIDX"}]
        return []

    def get_quote(self, exchange, token):
        return {**self._q.get(str(token), {}), "status": "OK"}


def test_resolve_index_sensex_is_bse():
    c = _FakeSDK()
    r = c.resolve_index("SENSEX")
    assert r["status"] == "OK" and r["exchange"] == "BSE" and r["token"] == "99919000"
    assert c.resolve_index("BANKEX")["exchange"] == "BSE"
    assert c.resolve_index("NIFTY")["status"] == "INSTRUMENT_MASTER_CONTRACT_NOT_FOUND"  # no NSE rows in fake


def test_option_universe_sensex_routes_to_bfo():
    c = _FakeSDK()
    uni = c._option_universe("SENSEX")
    assert uni["exchange"] == "BFO" and uni["quote_ex"] == "BFO"
    assert uni["types"] == ("OPTIDX",)
    assert uni["spot"] == 76150.0
