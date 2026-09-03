"""
PHASE 2 — real broker option greeks merged onto the live autoscalp chain.

Covers app.main._merge_broker_greeks: NIFTY legs get broker Delta/Gamma/Theta/
Vega/IV with provenance; MCX (capability UNAVAILABLE) legs stay None and are
tagged greeks_source=UNAVAILABLE. Nothing is fabricated, nothing becomes 0.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.main import _merge_broker_greeks


class _SDK:
    def __init__(self, cap=None, greeks=None):
        self._cap, self._greeks = cap or {}, greeks

    def greek_capabilities(self):
        return self._cap

    def get_option_greeks(self, und, expiry):
        if self._greeks is None:
            return {"status": "NO_DATA", "capability": "UNAVAILABLE", "rows": []}
        return {"status": "OK", "rows": self._greeks}


def _chain():
    mk = lambda k: {"strike": k,
                    "ce": {"ltp": 100.0, "oi": 5000, "iv": None, "delta": None,
                           "gamma": None, "theta": None, "vega": None},
                    "pe": {"ltp": 90.0, "oi": 4000, "iv": None, "delta": None,
                           "gamma": None, "theta": None, "vega": None}}
    return [mk(24000.0), mk(24050.0)]


def test_nifty_legs_get_real_broker_greeks():
    rows = [
        {"strikePrice": "24000", "optionType": "CE", "delta": "0.55", "gamma": "0.0013",
         "theta": "-14.2", "vega": "11.1", "impliedVolatility": "10.6"},
        {"strikePrice": "24000", "optionType": "PE", "delta": "-0.43", "gamma": "0.0016",
         "theta": "-7.8", "vega": "11.0", "impliedVolatility": "8.9"},
    ]
    chain = _chain()
    _merge_broker_greeks(_SDK(cap={"NIFTY": {"status": "AVAILABLE"}}, greeks=rows),
                         "NIFTY", "09SEP2026", chain)
    ce = chain[0]["ce"]
    assert ce["delta"] == 0.55 and ce["gamma"] == 0.0013 and ce["vega"] == 11.1
    assert ce["greeks_source"] == "BROKER"
    assert ce["data_source"]["delta"] == "ANGELONE_OPTION_GREEK"
    assert chain[0]["pe"]["delta"] == -0.43            # PE delta stays negative, not abs/zero
    # a strike with no matching greek row -> stays None, not 0
    assert chain[1]["ce"]["delta"] is None


def test_mcx_unavailable_leaves_greeks_null_and_tagged():
    chain = _chain()
    _merge_broker_greeks(_SDK(cap={"NATURALGAS": {"status": "UNAVAILABLE"}}),
                         "NATURALGAS", "25SEP2026", chain)
    for row in chain:
        for side in ("ce", "pe"):
            assert row[side]["delta"] is None
            assert row[side]["gamma"] is None
            assert row[side]["greeks_source"] == "UNAVAILABLE"


def test_unknown_capability_then_no_data_tags_unavailable():
    chain = _chain()
    # capability cache cold -> falls through to the call, which returns NO_DATA
    _merge_broker_greeks(_SDK(cap={}, greeks=None), "CRUDEOIL", "18SEP2026", chain)
    for row in chain:
        for side in ("ce", "pe"):
            assert row[side]["delta"] is None
            assert row[side]["greeks_source"] in ("UNAVAILABLE", "UNKNOWN")
